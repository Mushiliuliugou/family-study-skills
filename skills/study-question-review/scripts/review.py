#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only due-queue audit and guarded publication through existing parent task APIs."""
import argparse
from contextlib import closing
from datetime import date, datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen, ProxyHandler, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from uuid import uuid4

class Invalid(ValueError):pass
def check(value,message):
    if not value:raise Invalid(message)
def canonical(obj):return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def hashed(obj):return hashlib.sha256(canonical(obj).encode()).hexdigest()
def stamp():return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='microseconds')
def business_date(value):
    try:check(isinstance(value,str) and date.fromisoformat(value).isoformat()==value,'日期应为YYYY-MM-DD')
    except (ValueError,TypeError):raise Invalid('日期应为YYYY-MM-DD')
    return value
def connect(path):
    path=Path(path).resolve();check(path.is_file(),'指定的统一数据库不存在，不新建空库')
    db=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
    check(db.execute('PRAGMA user_version').fetchone()[0]==4,'仅支持家庭学习工作台schema=4数据库（应用功能版本另见兼容说明）')
    return db

def plan(database,on_date,limits=None,minutes=None):
    day=business_date(on_date);limits={} if limits is None else limits;minutes={} if minutes is None else minutes
    check(isinstance(minutes,dict) and all(isinstance(k,str) and type(v) in (int,float) and math.isfinite(v) and 0.5<=v<=20 for k,v in minutes.items()),'每题规划估时应为0.5—20分钟；过长大题先减少范围，不强制完成')
    check(isinstance(limits,dict) and all(isinstance(k,str) and type(v) is int and 0<=v<=30 for k,v in limits.items()) and sum(limits.values())<=100,'题量必须是每科0—30道、总计不超过100道的整数')
    with closing(connect(database)) as db:
        # One SQLite read transaction keeps questions, states and task reservations consistent.
        db.execute('BEGIN')
        subjects={r['id']:dict(r) for r in db.execute('SELECT * FROM subjects')}
        check(all(s in subjects and subjects[s]['enabled'] for s in set(limits)|set(minutes)),'题量中包含未启用或不存在的科目')
        settings={r['id']:r['value'] for r in db.execute("SELECT id,value FROM settings WHERE id IN ('data_epoch','review_rules','review_policy_version','test_database')")}
        check(bool(settings.get('data_epoch')),'缺少数据库标识')
        columns={r['name'] for r in db.execute('PRAGMA table_info(questions)')}
        kind_column='question_kind' if 'question_kind' in columns else "'unclassified'"
        priority_column='priority' if 'priority' in columns else "'U'"
        bank_filter=" WHERE bank_scope='error'" if 'bank_scope' in columns else ''
        note_column='priority_note' if 'priority_note' in columns else "''"
        # Reading rows never bring their original title, image references, notes or answers into this reader.
        questions=[dict(r) for r in db.execute(f"SELECT id,subject_id,status,created_at,updated_at,{kind_column} AS question_kind,CASE WHEN {kind_column}='reading_comprehension' THEN '阅读理解（仅分析）' ELSE title END AS title,CASE WHEN {kind_column}='reading_comprehension' THEN '[]' ELSE images END AS images,CASE WHEN {kind_column}='reading_comprehension' THEN 'U' ELSE {priority_column} END AS priority,CASE WHEN {kind_column}='reading_comprehension' THEN '' ELSE {note_column} END AS priority_note FROM questions{bank_filter} ORDER BY created_at,id")]
        states={r['question_id']:dict(r) for r in db.execute('SELECT * FROM review_state')}
        assets={r['id']:dict(r) for r in db.execute('SELECT * FROM assets')}
        tasks=[dict(r) for r in db.execute("SELECT * FROM tasks WHERE status!='cancelled'")]
        reserved={r[0] for r in db.execute("SELECT i.question_id FROM task_items i JOIN tasks t ON t.id=i.task_id WHERE t.status!='cancelled' AND (i.grade_result='' OR t.task_date=?)",(day,))}
        ungraded=[dict(r) for r in db.execute("SELECT t.id AS task_id,t.task_date,t.subject_id,i.id AS item_id,i.question_id,i.position,i.student_state FROM task_items i JOIN tasks t ON t.id=i.task_id WHERE t.status!='cancelled' AND i.grade_result='' ORDER BY t.task_date,t.id,i.position")]
        hinted={r[0].removeprefix('student_hint:') for r in db.execute("SELECT id FROM settings WHERE id LIKE 'student_hint:%'")}
        for item in ungraded:item['hint_recorded_result']='hint' if item['item_id'] in hinted else ''
        epoch=settings['data_epoch'];rules=json.loads(settings.get('review_rules','{}'));buckets={k:[] for k in ['ready','future','paused','quality_blocked','reserved','archived','inactive','analysis_only','data_issues']}
        for q in questions:
            qid=q['id'];sid=q['subject_id'];r=states.get(qid)
            item={'question_id':qid,'subject_id':sid,'title':q['title'],'question_version':q['updated_at'],'confirmed_priority':q.get('priority','U'),'priority_note':q.get('priority_note',''),'bank':'mastered' if r and r['reason']=='mastered' else 'wrong_questions'}
            if r:item.update(review_version=r['updated_at'],mode=r['mode'],due_date=r['due_date'],last_result=r['last_result'])
            if not r:item['issue']='缺少复习状态';buckets['data_issues'].append(item);continue
            if sid not in subjects:item['issue']='科目未登记';buckets['data_issues'].append(item);continue
            if r['mode'] in ('archived','trash'):buckets['archived'].append(item);continue
            if q.get('question_kind')=='reading_comprehension':buckets['analysis_only'].append(item);continue
            if q['status']!='active':buckets['quality_blocked'].append(item);continue
            if not subjects[sid]['enabled']:buckets['inactive'].append(item);continue
            if r['mode'] in ('hold','parent'):item['pause_reason']=r['reason'];buckets['paused'].append(item);continue
            if r['mode'] not in ('new','scheduled'):item['issue']='未知复习状态';buckets['data_issues'].append(item);continue
            try:
                images=json.loads(q['images']);prompts=[p for p in images if p['role'] in ('prompt','figure')]
                check(any(p['role']=='prompt' for p in prompts),'缺少题干')
                for pic in prompts:
                    asset=assets.get(pic['asset_id']);check(asset and (Path(database).resolve().parent/'media'/asset['filename']).is_file(),'题干图片未找到')
                if r['mode']=='scheduled':business_date(r['due_date'])
            except (Invalid,ValueError,KeyError,TypeError):item['issue']='题干图片或复习日期待核';buckets['data_issues'].append(item);continue
            if qid in reserved:buckets['reserved'].append(item);continue
            if r['mode']=='scheduled' and r['due_date']>day:buckets['future'].append(item);continue
            item['overdue_days']=max(0,(date.fromisoformat(day)-date.fromisoformat(r['due_date'])).days) if r['due_date'] else 0
            buckets['ready'].append(item)
        buckets['ready'].sort(key=lambda r:(0 if r['mode']=='scheduled' else 1,int(r['confirmed_priority'][1:]) if r['confirmed_priority']!='U' else 6,r['due_date'] or '',r['title'],r['question_id']))
        counts_by_task=dict(db.execute('SELECT task_id,COUNT(*) FROM task_items GROUP BY task_id'));allocated=sum(t.get('estimated_minutes',counts_by_task.get(t['id'],0)*5) for t in tasks if t['task_date']==day)
        selected=[];shortages=[];existing=[]
        for sid,count in limits.items():
            if not count:continue
            matches=[t for t in tasks if t['subject_id']==sid and t['task_date']==day]
            if matches:existing.extend({'task_id':t['id'],'subject_id':sid,'status':t['status']} for t in matches);continue
            ready=[r for r in buckets['ready'] if r['subject_id']==sid]
            if len(ready)<count:shortages.append({'subject_id':sid,'requested':count,'available':len(ready)});continue
            selected.extend({**r,'estimated_minutes':minutes.get(sid,5),'estimate_basis':'家长提供的平均估时' if sid in minutes else '未校准规划占位：每完整题块5分钟，非实测'} for r in ready[:count])
        new_minutes=sum(r['estimated_minutes'] for r in selected);budget_issues=[]
        if allocated+new_minutes>20+1e-9:budget_issues.append('当天各科合计预计超过20分钟，请减量或由家长校准估时，不要求完成原题数')
        time_budget={'hard_cap_minutes':20,'usual_target_minutes':15,'allocated_minutes':allocated,'new_minutes':new_minutes,'total_minutes':allocated+new_minutes,'remaining_minutes':max(0,20-allocated-new_minutes),'timer_enabled':False,'stop_rule':'实际达到20分钟即停止，可少做或不做；未完成不记错，不要求当日补齐'}
        payload=None
        if selected and not shortages and not existing and not budget_issues:
            payload={'task_date':day,'request_id':uuid4().hex,'subjects':[]}
            for sid,count in limits.items():
                items=[r for r in selected if r['subject_id']==sid]
                if items:payload['subjects'].append({'subject_id':sid,'count':len(items),'question_ids':[r['question_id'] for r in items],'versions':{r['question_id']:r['question_version'] for r in items},'estimated_minutes':sum(r['estimated_minutes'] for r in items)})
        result={'format':'study-question-review-plan','version':1,'created_at':stamp(),'task_date':day,'data_epoch':epoch,'database_path':str(Path(database).resolve()),
            'rule_source':'existing_database_configuration','rules_snapshot':rules,'new_priority_thresholds_enabled':False,'confirmed_priority_order_enabled':True,'review_policy_version':settings.get('review_policy_version','legacy'),'limits':limits,'minutes_per_unit':minutes,'time_budget':time_budget,'budget_issues':budget_issues,'counts':{k:len(v) for k,v in buckets.items()},'inventory_total':len(questions),
            'ready_breakdown':{'due':sum(r['mode']=='scheduled' for r in buckets['ready']),'unassigned':sum(r['mode']=='new' for r in buckets['ready'])},'buckets':buckets,'selected':selected,'ungraded_task_items':ungraded,'shortages':shortages,'existing_daily_tasks':existing,'publish_payload':payload,
            'data_issues_block_publication':bool(buckets['data_issues']),'logging_scope':'现有任务及反馈记录；全动作统一事件流水尚未接入','mutation_performed':False}
        result['plan_sha256']=hashed(result)
        check(sum(result['counts'].values())==len(questions),'队列核对不平衡')
        return result

def recommend(database,on_date,target_minutes=15,minutes=None):
    check(type(target_minutes) in (int,float) and math.isfinite(target_minutes) and 0<=target_minutes<=20,'每日推荐目标应为0—20分钟；0表示今天不安排')
    minutes={} if minutes is None else minutes;base=plan(database,on_date,minutes=minutes)
    day=date.fromisoformat(on_date);start=(day-timedelta(days=day.weekday())).isoformat();end=(day+timedelta(days=6-day.weekday())).isoformat()
    with closing(connect(database)) as db:
        counts={sid:0 for sid in minutes}
        coverage={r['id']:set() for r in db.execute('SELECT id FROM subjects WHERE enabled=1')}
        for r in db.execute("SELECT DISTINCT t.subject_id,t.task_date FROM tasks t JOIN task_items i ON i.task_id=t.id WHERE t.status!='cancelled' AND i.student_state='submitted' AND t.task_date BETWEEN ? AND ?",(start,end)):
            if r['subject_id'] in coverage:coverage[r['subject_id']].add(r['task_date'])
    occupied={r['subject_id'] for r in base['ungraded_task_items'] if r['task_date']==on_date}
    with closing(connect(database)) as db:
        occupied|={r[0] for r in db.execute("SELECT subject_id FROM tasks WHERE task_date=? AND status!='cancelled'",(on_date,))}
    available={}
    for row in base['buckets']['ready']:
        if row['subject_id'] not in occupied:available.setdefault(row['subject_id'],[]).append(row)
    allowance=max(0,min(target_minutes,20)-base['time_budget']['allocated_minutes']);spent=0;counts={};skipped_long=[]
    subject_order=sorted(available,key=lambda sid:(not any(r['mode']=='scheduled' for r in available[sid]),len(coverage.get(sid,set()))>=2,len(coverage.get(sid,set())),available[sid][0]['due_date'] or '9999',sid))
    # At most two subjects are recommended in one short supportive session; no daily all-subject quota.
    candidates=subject_order[:2]
    while candidates:
        progress=False
        for sid in candidates:
            cost=minutes.get(sid,5);index=counts.get(sid,0)
            if index<min(len(available[sid]),30) and spent+cost<=allowance+1e-9:
                counts[sid]=index+1;spent+=cost;progress=True
            elif index<len(available[sid]) and cost>allowance:skipped_long.append(sid)
        if not progress:break
    result=plan(database,on_date,counts,minutes)
    result['recommendation']={'target_minutes':target_minutes,'weekly_soft_target':2,'week_start':start,'coverage_days':{s:len(days) for s,days in coverage.items()},'subjects_with_ready_questions':subject_order,'long_units_deferred':sorted(set(skipped_long)),
        'basis':'按到期、家长确认优先级、各科近期练习覆盖和家长规划估时推荐，最多两科；每科每周两次只是有需求时的轮换目标，不补欠作业、不强行凑满15或20分钟；学校已有同类独立练习可少做或不做'}
    result.pop('plan_sha256');result['plan_sha256']=hashed(result);return result

def read_plan(path):
    path=Path(path);check(path.is_file() and path.stat().st_size<=16*1024**2,'预览文件不存在或过大');p=json.loads(path.read_text())
    check(isinstance(p,dict) and p.get('format')=='study-question-review-plan' and p.get('version')==1,'不是支持的重温预览')
    expected=p.pop('plan_sha256',None);check(expected==hashed(p),'预览文件已改变，请重新生成');p['plan_sha256']=expected
    check(p.get('publish_payload') and not p.get('shortages') and not p.get('existing_daily_tasks') and not p.get('data_issues_block_publication') and not p.get('budget_issues'),'此预览不可发布：请处理题量、已有任务或数据问题')
    check(p.get('time_budget',{}).get('hard_cap_minutes')==20 and p['time_budget']['total_minutes']<=20,'请重新生成符合每日20分钟上限的预览')
    return p

def local_url(value):
    u=urlsplit(value);check(u.scheme=='http' and u.hostname in ('localhost','127.0.0.1') and u.port and u.path in ('','/') and not u.query and not u.fragment and not u.username,'只支持本机工作台根地址，如http://127.0.0.1:8765')
    return value.rstrip('/')
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):raise Invalid('本机工作台不应重定向，停止发送资料')

def request(base,path,body=None,epoch=''):
    headers={'X-Study-Role':'parent'}
    if body is not None:headers.update({'Content-Type':'application/json','Origin':base,'X-Study-Client':'local','X-Study-Epoch':epoch})
    req=Request(base+'/api/'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    try:
        # No proxies or third-party redirects are used for local family data.
        with build_opener(ProxyHandler({}),NoRedirect()).open(req,timeout=30) as response:return json.load(response)
    except HTTPError as e:
        try:message=json.loads(e.read()).get('error',str(e))
        except Exception:message=str(e)
        raise Invalid(message)

def publish(plan_path,base,allow_formal=False):
    p=read_plan(plan_path);base=local_url(base);bootstrap=request(base,'review-context');settings=bootstrap['settings']
    check(settings.get('data_epoch')==p['data_epoch'],'服务连接的数据库与预览不一致，或已恢复资料；请重新预览')
    check(allow_formal or settings.get('test_database')=='true','正式库发布需已有家长授权，并明确加--allow-formal')
    check(settings.get('review_policy_version','legacy')==p.get('review_policy_version','legacy'),'重温规则版本已变化，请重启工作台并重新预览')
    check(bootstrap.get('rules')==p['rules_snapshot'],'复习规则已变化，请重新预览')
    payload=p['publish_payload'];tasks=request(base,'tasks')['tasks'];prior=[t for t in tasks if t['request_id']==payload['request_id']]
    if not prior:
        already=sum(t.get('estimated_minutes',t['total']*5) for t in tasks if t['task_date']==p['task_date'] and t['status']!='cancelled')
        check(already+sum(s['estimated_minutes'] for s in payload['subjects'])<=20+1e-9,'当天已有任务预计用时变化，合计超过20分钟，请重新预览并减量')
        questions={q['id']:q for q in bootstrap['questions']};reviews=bootstrap['review_states']
        for row in p['selected']:
            qid=row['question_id'];check(qid in questions and questions[qid]['updated_at']==row['question_version'] and qid in reviews and reviews[qid]['updated_at']==row['review_version'],'题目或复习状态已变化，请重新预览')
    # Existing central method checks task ownership, quotas, quality, versions and request idempotency.
    result=request(base,'task-publish',payload,p['data_epoch'])
    return {'format':'study-question-review-receipt','version':1,'action':'task_published','created_at':stamp(),'request_id':payload['request_id'],'plan_sha256':p['plan_sha256'],
        'data_epoch':p['data_epoch'],'task_date':p['task_date'],'task_ids':result['task_ids'],'reused':result['reused'],'time_budget':p['time_budget'],'review_policy_version':p.get('review_policy_version','legacy'),'learning_result_recorded':False}

def write_report(out,result):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);name=('week' if result['format']=='family-study-weekly' else 'plan' if result['format']=='study-question-review-plan' else 'publish')+'-'+uuid4().hex[:8]+'.json';path=out/name
    with path.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    if result['format']=='study-question-review-plan':
        lines=['# 错题重温预览','',f'日期：{result["task_date"]}；本文件不改变复习日期或记录学习结果。','',f'全库{result["inventory_total"]}题，分类合计{sum(result["counts"].values())}题；已选{len(result["selected"])}题。','', '| 状态 | 数量 |','| --- | ---: |']
        labels={'ready':'到期或未安排，可选','future':'尚未到期','paused':'家长处理/待核对','quality_blocked':'题面待核','reserved':'其他任务占用或同日已分配','archived':'归档/回收站','analysis_only':'阅读理解仅分析，不练习','inactive':'未启用科目','data_issues':'数据缺口'}
        lines.extend(f'| {labels[k]} | {v} |' for k,v in result['counts'].items())
        lines.extend(['',f'可选清单中：已到期{result["ready_breakdown"]["due"]}题、尚未安排{result["ready_breakdown"]["unassigned"]}题。','现有规则快照：'+json.dumps(result['rules_snapshot'],ensure_ascii=False),'每天各科合计最多20分钟，通常推荐15分钟，可少做或不做；普通钟表把关，达到上限就停。','本次规划约'+str(result['time_budget']['new_minutes'])+'分钟；当天已有约'+str(result['time_budget']['allocated_minutes'])+'分钟；估时不是实测。','新P等级门槛未启用；统一全动作事件流水仍待接入。','', '## 本次选题','', '| 科目 | 题目 | 原到期日 | 积压天数 |','| --- | --- | --- | ---: |'])
        lines.extend(f'| {r["subject_id"]} | {r["title"].replace("|","/")} | {r["due_date"] or "未安排"} | {r["overdue_days"]} |' for r in result['selected'])
        if result.get('recommendation'):lines.append('\n推荐依据：'+result['recommendation']['basis'])
        if result.get('budget_issues'):lines.extend(['','；'.join(result['budget_issues'])])
        if result['shortages']:lines.extend(['','题量不足：'+json.dumps(result['shortages'],ensure_ascii=False)])
        if result['existing_daily_tasks']:lines.extend(['','当天已有任务：'+json.dumps(result['existing_daily_tasks'],ensure_ascii=False)])
        if result['data_issues_block_publication']:lines.append('\n存在数据缺口，请处理后重新生成预览。')
        with path.with_suffix('.md').open('x') as f:f.write('\n'.join(lines))
    return str(path)

def main():
    parser=argparse.ArgumentParser(description='统一错题库的重温预览与家长任务发布');sub=parser.add_subparsers(dest='command',required=True)
    q=sub.add_parser('plan');q.add_argument('--database',required=True);q.add_argument('--date',default=datetime.now(timezone(timedelta(hours=8))).date().isoformat());q.add_argument('--limits',default='{}',help='家长指定的科目题量JSON；留空只盘点，不生成发布任务');q.add_argument('--minutes',default='{}',help='按科每题平均规划分钟，非计时');q.add_argument('--recommend',action='store_true');q.add_argument('--target-minutes',type=float,default=15);q.add_argument('--out',required=True)
    q=sub.add_parser('publish');q.add_argument('--plan',required=True);q.add_argument('--base-url',required=True);q.add_argument('--out',required=True);q.add_argument('--commit',action='store_true',required=True);q.add_argument('--allow-formal',action='store_true')
    q=sub.add_parser('week');q.add_argument('--base-url',required=True);q.add_argument('--start-date',required=True);q.add_argument('--days',type=int,default=7);q.add_argument('--limit',type=int,default=10);q.add_argument('--daily-minutes',type=float,default=15);q.add_argument('--minutes-per-question',type=float,default=5);q.add_argument('--subjects',nargs='+');q.add_argument('--out',required=True)
    args=parser.parse_args()
    try:
        if args.command=='plan':
            check(not args.recommend or args.limits=='{}','自动推荐和固定题量不能同时指定')
            r=recommend(args.database,args.date,args.target_minutes,json.loads(args.minutes)) if args.recommend else plan(args.database,args.date,json.loads(args.limits),json.loads(args.minutes))
        elif args.command=='week':
            body={'start_date':business_date(args.start_date),'days':args.days,'limit':args.limit,'daily_minutes':args.daily_minutes,'minutes_per_question':args.minutes_per_question}
            if args.subjects:body['subject_ids']=args.subjects
            r=request(local_url(args.base_url),'weekly-prepare',body,request(local_url(args.base_url),'review-context')['settings']['data_epoch'])
        else:r=publish(args.plan,args.base_url,args.allow_formal)
        path=write_report(args.out,r);print(json.dumps({'report':path,'format':r['format'],'counts':r.get('counts'),'selected':r.get('selected_count',len(r.get('selected',[]))),'task_ids':r.get('task_ids'),'reused':r.get('reused')},ensure_ascii=False))
    except (Invalid,ValueError,KeyError,TypeError,OSError,sqlite3.Error) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
