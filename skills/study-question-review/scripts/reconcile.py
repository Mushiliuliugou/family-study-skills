#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reconcile committed intakes, handoff files, review queues and task feedback without writes."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from uuid import uuid4
from review import plan,connect,business_date,stamp,hashed,check,Invalid

def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def load_handoff(path):
    path=Path(path);check(path.is_file() and path.stat().st_size<=8*1024**2,'交接文件不存在或过大');r=json.loads(path.read_text())
    check(isinstance(r,dict) and r.get('format')=='study-question-ingest-to-review' and r.get('version')==1,'交接文件格式不正确')
    claimed=r.pop('handoff_sha256',None);check(claimed==hashed(r),'交接文件已改变或损坏');r['handoff_sha256']=claimed
    check(r.get('producer_skill')=='study-question-ingest' and r.get('consumer_skill')=='study-question-review','Skill交接方向不正确')
    check(isinstance(r.get('items'),list) and 1<=len(r['items'])<=100 and r.get('question_count')==len(r['items']),'交接数量不正确')
    check(isinstance(r.get('source_batch_id'),str) and re.fullmatch('[a-f0-9]{32}',r['source_batch_id']),'来源批次编号不正确')
    ids=[]
    for item in r['items']:
        check(isinstance(item,dict) and isinstance(item.get('question_id'),str) and re.fullmatch('[a-f0-9]{32}',item['question_id']),'交接题号不正确')
        check(item.get('classification_status')=='unverified' and item.get('priority_status')=='not_confirmed','不接受把智能体建议伪装成家长已确认的交接')
        source_ids=item.get('source_intake_ids');check(isinstance(source_ids,list) and source_ids and all(isinstance(x,str) and re.fullmatch('[a-f0-9]{32}',x) for x in source_ids) and len(source_ids)==len(set(source_ids)),'交接来源收题编号不正确')
        ids.append(item['question_id'])
    check(len(ids)==len(set(ids)),'交接题号重复');return r

def reconcile(database,on_date,handoffs=(),verify_media=False):
    day=business_date(on_date);input_reports=[];seen=set();duplicates=[]
    for path in handoffs:
        item=load_handoff(path);key=(item['source_batch_id'],item['source_receipt_sha256'])
        if key in seen:duplicates.append(item['handoff_id']);continue
        seen.add(key);input_reports.append(item)
    with closing(connect(database)) as db:
        version=db.execute('PRAGMA data_version').fetchone()[0];db.execute('BEGIN')
        epoch=db.execute("SELECT value FROM settings WHERE id='data_epoch'").fetchone()[0]
        cols={r['name'] for r in db.execute('PRAGMA table_info(questions)')};kind_column='question_kind' if 'question_kind' in cols else "'unclassified'";annotations='agent_annotations' if 'agent_annotations' in cols else "'[]'";scope_column='bank_scope' if 'bank_scope' in cols else "'error'"
        questions={r['id']:dict(r) for r in db.execute(f"SELECT id,subject_id,updated_at,{scope_column} AS bank_scope,{kind_column} AS question_kind,CASE WHEN {kind_column}='reading_comprehension' THEN '[]' ELSE images END AS images,CASE WHEN {kind_column}='reading_comprehension' THEN '{{}}' ELSE source_ref END AS source_ref,CASE WHEN {kind_column}='reading_comprehension' THEN '[]' ELSE {annotations} END AS agent_annotations FROM questions")}
        intakes={r['id']:dict(r) for r in db.execute('SELECT id,question_id FROM intake_records')}
        states={r['question_id']:dict(r) for r in db.execute('SELECT question_id,updated_at FROM review_state')};all_tasks={r['id']:dict(r) for r in db.execute('SELECT * FROM tasks')}
        task_items=[dict(r) for r in db.execute('SELECT id,task_id,question_id,position,student_state,grade_result,grade_event_id,grade_batch_id FROM task_items')];batches={r['id']:dict(r) for r in db.execute('SELECT * FROM grade_batches')};log_ids={r['id']:dict(r) for r in db.execute('SELECT id,question_id,result,undone FROM review_log')}
        queue=plan(database,day);classification={r['question_id']:name for name,items in queue['buckets'].items() for r in items};issues=[{'kind':'question_queue_data_issue',**i} for i in queue['buckets']['data_issues']];handoff_results=[]
        classification.update({qid:'strengthening_bank' for qid,q in questions.items() if q.get('bank_scope')=='strengthening'})
        check(epoch==queue['data_epoch'] and set(questions)==set(classification),'扫描时资料发生变化，请重跑对账')
        for handoff in input_reports:
            check(handoff['data_epoch']==epoch,'交接文件属于其他数据库或恢复前版本')
            reading_ids=[i['question_id'] for i in handoff['items'] if questions.get(i['question_id'],{}).get('question_kind')=='reading_comprehension']
            if reading_ids:
                issues.append({'kind':'reading_handoff_requires_summary','question_ids':reading_ids})
                for i in handoff['items']:handoff_results.append({'handoff_id':handoff['handoff_id'],'source_batch_id':handoff['source_batch_id'],'question_id':i['question_id'],'subject_id':i['subject_id'],'current_bucket':classification.get(i['question_id'],'missing_question'),'changes':['请改用不含原题的阅读分析摘要；混合批次请重新导出普通题'],'automatically_scheduled':False})
                continue
            receipt_row=db.execute('SELECT value FROM settings WHERE id=?',('study_ingest:'+handoff['source_batch_id'],)).fetchone();raw=receipt_row[0] if receipt_row else None;check(raw is not None,'数据库中找不到该交接对应的已提交入库回执')
            r=json.loads(raw);check(r.get('status')=='applied','交接来源批次已撤销或并未提交')
            check(hashed(r)==handoff['source_receipt_sha256'],'交接回执与数据库内提交事实不符')
            source={x['question_id']:x for x in r.get('changes',[])};check(set(source)=={x['question_id'] for x in handoff['items']},'交接题号与已提交明细不一致')
            for item in handoff['items']:
                qid=item['question_id'];q=questions.get(qid);status=classification.get(qid,'missing_question');flags=[]
                if q is None:issues.append({'kind':'handoff_question_missing','question_id':qid})
                elif q['subject_id']!=item.get('subject_id'):issues.append({'kind':'handoff_subject_changed','question_id':qid,'expected':item.get('subject_id'),'actual':q['subject_id']})
                else:
                    if q['updated_at']!=item.get('question_version'):flags.append('question_changed_since_handoff')
                    if states.get(qid,{}).get('updated_at')!=item.get('review_snapshot',{}).get('updated_at'):flags.append('review_changed_since_handoff')
                    entries=json.loads(q.get('agent_annotations','[]'));entry=next((a for a in entries if a.get('batch_id')==handoff['source_batch_id']),None)
                    if not entry or sorted(entry.get('source_intake_ids',[]))!=sorted(item['source_intake_ids']):issues.append({'kind':'handoff_annotation_or_source_missing','question_id':qid})
                    for iid in item.get('source_intake_ids',[]):
                        if iid not in intakes or intakes[iid]['question_id']!=qid:issues.append({'kind':'handoff_intake_missing','question_id':qid,'intake_id':iid})
                handoff_results.append({'handoff_id':handoff['handoff_id'],'source_batch_id':handoff['source_batch_id'],'question_id':qid,'subject_id':q['subject_id'] if q else item.get('subject_id'),'current_bucket':status,'changes':flags,'automatically_scheduled':False})
        resources={r['id']:dict(r) for r in db.execute('SELECT id,path,page_count FROM resources')};source_groups={'with_intake_receipt':[],'with_source_reference':[],'without_source_reference':[],'invalid_source_reference':[],'reading_analysis_only':[],'strengthening_bank':[]}
        q_intakes={r['question_id'] for r in intakes.values()}
        for qid,q in questions.items():
            if q.get('bank_scope')=='strengthening':
                source_groups['strengthening_bank'].append(qid)
                if not db.execute("SELECT 1 FROM settings WHERE id=?",('strengthening:'+qid,)).fetchone():issues.append({'kind':'strengthening_metadata_missing','question_id':qid})
                continue
            if q.get('question_kind')=='reading_comprehension':source_groups['reading_analysis_only'].append(qid);continue
            if qid in q_intakes:source_groups['with_intake_receipt'].append(qid)
            else:
                try:ref=json.loads(q.get('source_ref','{}'))
                except (ValueError,TypeError):ref=None
                if isinstance(ref,dict) and ref:
                    resource=resources.get(ref.get('resource_id'));page=ref.get('page')
                    if resource and type(page) is int and 1<=page<=resource['page_count']:
                        source_groups['with_source_reference'].append(qid)
                        if not Path(resource['path']).is_file():issues.append({'kind':'source_file_missing','question_id':qid,'resource_id':resource['id']})
                    else:source_groups['invalid_source_reference'].append(qid);issues.append({'kind':'source_reference_invalid','question_id':qid})
                elif ref=={}:
                    source_groups['without_source_reference'].append(qid);issues.append({'kind':'question_without_traceable_source','question_id':qid})
                else:source_groups['invalid_source_reference'].append(qid);issues.append({'kind':'source_reference_invalid','question_id':qid})
        for iid,r in intakes.items():
            if r['question_id'] not in questions:issues.append({'kind':'orphan_intake','intake_id':iid,'question_id':r['question_id']})
        ungraded=[];reading_task_items=[]
        hinted={r[0].removeprefix('student_hint:') for r in db.execute("SELECT id FROM settings WHERE id LIKE 'student_hint:%'")}
        for i in task_items:
            t=all_tasks.get(i['task_id']);qid=i['question_id']
            if not t or qid not in questions:issues.append({'kind':'orphan_task_item','item_id':i['id'],'question_id':qid});continue
            if t['status']=='cancelled':continue
            if qid in source_groups['reading_analysis_only'] and not i['grade_result']:
                reading_task_items.append({'task_id':t['id'],'question_id':qid,'item_id':i['id'],'next_action':'仅作历史保留，不要求学生完成；按阅读分析处理'});continue
            if not i['grade_result']:
                ungraded.append({'task_id':t['id'],'task_date':t['task_date'],'subject_id':t['subject_id'],'question_id':qid,'position':i['position'],'student_state':i['student_state'],'hint_recorded_result':'hint' if i['id'] in hinted else '', 'next_action':'parent_feedback' if i['student_state']=='submitted' else 'student_completion_or_parent_check'})
            else:
                event=log_ids.get(i['grade_event_id'])
                if not event or event['question_id']!=qid or event['result']!=i['grade_result'] or event['undone']:
                    issues.append({'kind':'feedback_event_missing_or_inconsistent','task_id':t['id'],'item_id':i['id'],'question_id':qid})
                batch=batches.get(i['grade_batch_id'])
                if not batch or batch['task_id']!=t['id'] or batch['undone'] or i['id'] not in json.loads(batch['item_ids']):issues.append({'kind':'feedback_batch_missing_or_inconsistent','task_id':t['id'],'item_id':i['id'],'question_id':qid})
        for t in all_tasks.values():
            if t['status']!='cancelled' and not any(i['task_id']==t['id'] for i in task_items):issues.append({'kind':'active_task_without_items','task_id':t['id']})
            if t['status']=='graded' and any(i['task_id']==t['id'] and not i['grade_result'] for i in task_items):issues.append({'kind':'graded_task_has_ungraded_items','task_id':t['id']})
        checked=0
        if verify_media:
            assets={r['id']:dict(r) for r in db.execute('SELECT id,filename,sha256 FROM assets')};used=set()
            for qid,q in questions.items():
                for image in json.loads(q['images']):
                    for key in ['asset_id','original_id']:
                        aid=image[key]
                        if aid in used:continue
                        used.add(aid);a=assets.get(aid);path=Path(database).resolve().parent/'media'/a['filename'] if a else None
                        if not path or not path.is_file() or file_sha(path)!=a['sha256']:issues.append({'kind':'media_missing_or_corrupt','asset_id':aid,'question_id':qid})
                        checked+=1
        db.rollback();check(db.execute('PRAGMA data_version').fetchone()[0]==version,'扫描期间有其他操作提交，请重跑以取得一致对账结果')
    report={'format':'study-question-reconciliation','version':1,'created_at':stamp(),'as_of_date':day,'data_epoch':epoch,'status':'needs_attention' if issues or queue['counts']['data_issues'] else 'balanced',
        'database_written':False,'new_priority_rules_applied':False,'inventory_total':len(questions),'queue_counts':queue['counts'],'ready_breakdown':queue['ready_breakdown'],'source_coverage_counts':{k:len(v) for k,v in source_groups.items()},
        'source_coverage':source_groups,'duplicate_handoff_inputs':duplicates,'handoff_item_count':len(handoff_results),'handoff_results':handoff_results,'ungraded_items':ungraded,'due_items':[i for i in queue['buckets']['ready'] if i['mode']=='scheduled'],'paused_items':queue['buckets']['paused'],'quality_blocked_items':queue['buckets']['quality_blocked'],'issues':issues,'media_records_checked':checked,'reading_originals_skipped':len(source_groups['reading_analysis_only']),'reading_task_items_excluded':reading_task_items,
        'not_yet_implemented':['全动作统一追加式数据库事件流水','完整的学生提交/跳过历次变化记录','经家长确认的正式P字段与评级变更事件'],
        'notes':['阅读类只核对编号与科目，不读取原题、旧任务题面、原文、题图或答案；阅读图片不计入本次图片校验范围','balanced只表示本次快照中所核对的数量及关联平衡，不代表孩子已完成学习或不存在所有历史遗漏','旧导入有source_ref但无intake_records时单独列为历史来源可追溯，不直接判漏入库','无日期且mode=new是明确未安排，不自动算遗失或逾期','交接后家长核对或正常复习会产生版本变化，以当前库为准，旧回执不直接授权安排']}
    report['reconciliation_sha256']=hashed(report);return report

def write(out,report):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);name='reconciliation-'+uuid4().hex[:10];path=out/(name+'.json')
    with path.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    if report.get('format')=='study-question-reconciliation':
        lines=['# 错题交接与对账清单','',f'日期：{report["as_of_date"]}；本次只读，不写正式数据。',f'状态：{report["status"]}；题库共{report["inventory_total"]}题，队列合计{sum(report["queue_counts"].values())}题。','', '| 分类 | 数量 |','| --- | ---: |']
        names={'ready':'到期/未安排可选','future':'尚未到期','paused':'反馈待核对/家长处理','quality_blocked':'题面待核','reserved':'任务占用或同日已分配','archived':'归档/回收站','analysis_only':'阅读理解仅分析，不练习','inactive':'未启用科目','data_issues':'数据缺口'}
        lines.extend(f'| {names[k]} | {v} |' for k,v in report['queue_counts'].items())
        lines.extend(['',f'其中到期{report["ready_breakdown"]["due"]}题，未安排{report["ready_breakdown"]["unassigned"]}题；任务内仍未反馈{len(report["ungraded_items"])}项。','', '## 来源覆盖',json.dumps(report['source_coverage_counts'],ensure_ascii=False),'', '## 已到期但未分配',*['- '+r['title']+'；原到期日 '+r['due_date']+'；积压'+str(r['overdue_days'])+'天' for r in report['due_items']],'', '## 等待反馈的任务',*['- '+r['task_date']+' / '+r['subject_id']+' / 任务'+r['task_id'][:8]+'第'+str(r['position'])+'题：'+({'submitted':'已提交，待家长核对','pending':'待学生完成','skipped':'已跳过，待后续处理'}.get(r['student_state'],r['student_state'])) for r in report['ungraded_items']],'', '## 跨Skill交接'])
        if not report['handoff_results']:lines.append('本次未提供新入库批次交接文件；仅对现有库盘点，不伪造新批次。')
        lines.extend(f'- {r["subject_id"]} / {r["question_id"][:8]}：{r["current_bucket"]}；变化：'+('、'.join(r['changes']) or '无') for r in report['handoff_results'])
        lines.extend(['','## 缺口与异常']+[('- '+json.dumps(i,ensure_ascii=False)) for i in report['issues']])
        if not report['issues']:lines.append('所核对关联未发现异常；仍需留意下列未实现的完整流水范围。')
        lines.extend(['','## 尚未接入的记录范围']+['- '+s for s in report['not_yet_implemented']])
        lines.extend(['','## 说明']+['- '+s for s in report['notes']]);path.with_suffix('.md').write_text('\n'.join(lines))
    return str(path)

def main():
    p=argparse.ArgumentParser(description='两个错题Skill的交接与只读对账');p.add_argument('--database',required=True);p.add_argument('--date',required=True);p.add_argument('--handoff',action='append',default=[]);p.add_argument('--verify-media',action='store_true');p.add_argument('--out',required=True);a=p.parse_args()
    try:
        r=reconcile(a.database,a.date,a.handoff,a.verify_media);path=write(a.out,r);print(json.dumps({'file':path,'status':r['status'],'inventory':r['inventory_total'],'handoff_items':r['handoff_item_count'],'issues':len(r['issues'])},ensure_ascii=False))
    except (Invalid,ValueError,KeyError,TypeError,OSError,sqlite3.Error) as e:
        failure={'format':'study-question-reconciliation-failure','version':1,'created_at':stamp(),'status':'blocked','error':str(e),'database_written':False}
        try:failure['failure_receipt']=write(a.out,failure)
        except OSError:pass
        print(json.dumps(failure,ensure_ascii=False),file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
