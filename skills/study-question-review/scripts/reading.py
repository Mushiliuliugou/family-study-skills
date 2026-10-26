#!/usr/bin/env python3
"""Read-only reading analysis summaries. Never select original question content or assets."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import date,datetime,timezone,timedelta
import json
from pathlib import Path
import re
import sqlite3
import sys
from uuid import uuid4

KEY='reading_analysis:'
ALLOWED={'id','subject_id','question_kind','reading_category','analysis_text','knowledge_points','reason_codes','reason_note','analysis_origin','confirmed','origin','created_at','practice_eligible','fingerprint'}
REASONS={'concept','reading','method','calculation','expression','unfamiliar','unsure'}
STATUS={'confirmed_agent':'家长已确认的智能体分析','pending_agent':'智能体分析待家长确认','classification_only':'仅分类，待补智能体分析'}
class Invalid(ValueError):pass

def require(value,message):
    if not value:raise Invalid(message)

def day(value):
    try:require(isinstance(value,str) and date.fromisoformat(value).isoformat()==value,'日期应为YYYY-MM-DD')
    except (ValueError,TypeError):raise Invalid('日期应为YYYY-MM-DD')
    return value

def connect(database):
    database=Path(database).resolve();require(database.is_file(),'统一数据库不存在，不另建主库')
    db=sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
    return db

def checked_record(key,value,subjects):
    require(isinstance(value,dict) and not set(value)-ALLOWED,'记录含未约定字段，已隔离待核')
    require(isinstance(value.get('id'),str) and re.fullmatch('[a-f0-9]{32}',value['id']) and key==KEY+value['id'],'记录编号不一致')
    require(value.get('question_kind')=='reading_comprehension' and value.get('practice_eligible') is False,'阅读分析范围标记不正确')
    require(value.get('subject_id') in subjects,'记录科目不存在')
    category=value.get('reading_category');text=value.get('analysis_text','');points=value.get('knowledge_points',[]);codes=value.get('reason_codes',[])
    require(isinstance(category,str) and 1<=len(category.strip())<=120,'阅读分类缺失或超长')
    require(isinstance(text,str) and len(text)<=4000,'分析摘要格式不正确')
    require(isinstance(points,list) and len(points)<=10 and all(isinstance(p,str) and len(p)<=80 for p in points),'知识点格式不正确')
    require(not re.search(r'https?://|file://|data:image|/media/|<img|!\[',' '.join([category,text,*points]),re.I),'摘要包含链接或图片标记，已隔离待核')
    require(isinstance(codes,list) and len(codes)<=7 and all(isinstance(c,str) and c in REASONS for c in codes),'分类标签格式不正确')
    require(value.get('analysis_origin') in ('parent','agent','student') and value.get('origin') in ('parent','student') and type(value.get('confirmed')) is bool,'分析来源或核对状态不正确')
    require(not (value['origin']=='student' and (value['analysis_origin']!='student' or value['confirmed'])),'学生自述不能冒充已确认分析')
    created=value.get('created_at')
    try:
        parsed=datetime.fromisoformat(created);require(parsed.tzinfo is not None,'记录时间缺少时区')
    except (ValueError,TypeError):raise Invalid('记录时间不正确')
    return parsed.astimezone(timezone(timedelta(hours=8))).date().isoformat()

def summarize(database,subject=None,date_from=None,date_to=None):
    if date_from:day(date_from)
    if date_to:day(date_to)
    require(not(date_from and date_to) or date_from<=date_to,'开始日期不能晚于结束日期')
    with closing(connect(database)) as db:
        db.execute('BEGIN')
        require({'settings','subjects'}<={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},'不是兼容的统一数据库')
        subjects={r['id']:r['name'] for r in db.execute('SELECT id,name FROM subjects')}
        require(subject is None or subject in subjects,'科目不存在')
        epoch=db.execute("SELECT value FROM settings WHERE id='data_epoch'").fetchone()
        require(epoch is not None,'缺少数据库标识')
        records=[];issues=[];matched=0;outside=0;total=0
        for row in db.execute("SELECT id,value FROM settings WHERE id LIKE 'reading_analysis:%' ORDER BY id"):
            total+=1
            try:
                v=json.loads(row['value']);created_day=checked_record(row['id'],v,subjects)
            except (Invalid,ValueError,TypeError):
                record_id=row['id'].removeprefix(KEY)
                issues.append({'record_id':record_id if re.fullmatch('[a-f0-9]{32}',record_id) else None,'code':'invalid_reading_record','message':'分析记录格式异常，已隔离；请家长核对。'})
                continue
            if (subject and v['subject_id']!=subject) or (date_from and created_day<date_from) or (date_to and created_day>date_to):outside+=1;continue
            matched+=1;agent=v['analysis_origin']=='agent' and bool(v.get('analysis_text','').strip())
            status=('confirmed_agent' if v['confirmed'] else 'pending_agent') if agent else 'classification_only'
            records.append({'record_id':v['id'],'subject_id':v['subject_id'],'subject_name':subjects[v['subject_id']],'reading_category':v['reading_category'].strip(),'analysis_text':v.get('analysis_text','').strip() if agent else '',
                'knowledge_points':v.get('knowledge_points',[]) if v['analysis_origin']!='student' else [],'reason_codes':v.get('reason_codes',[]) if agent else [],'analysis_origin':v['analysis_origin'],'parent_confirmed':v['confirmed'],'status':status,'recorded_on':created_day,'practice_eligible':False})
        legacy=[]
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'questions' in tables and 'question_kind' in {r['name'] for r in db.execute('PRAGMA table_info(questions)')}:
            # Explicitly select identifiers only. Do not inspect title, images, answer, note or source.
            for q in db.execute("SELECT id,subject_id FROM questions WHERE question_kind='reading_comprehension' ORDER BY id"):
                if not subject or q['subject_id']==subject:legacy.append({'question_id':q['id'],'subject_id':q['subject_id'],'message':'旧题仅有阅读类型标记，需家长补入独立分类与分析；不读取原题，也不猜测与现有摘要的对应关系。'})
    classifications=Counter((r['subject_id'],r['reading_category'],r['parent_confirmed']) for r in records)
    status_counts={key:sum(r['status']==key for r in records) for key in STATUS}
    return {'format':'study-reading-analysis-summary','version':1,'generated_at':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),'data_epoch':epoch[0],'filters':{'subject_id':subject,'date_from':date_from,'date_to':date_to},
        'source_record_total':total,'included_record_count':matched,'outside_filter_count':outside,'quarantined_record_count':len(issues),'status_counts':status_counts,
        'classifications':[{'subject_id':sid,'subject_name':subjects[sid],'reading_category':category,'parent_confirmed':confirmed,'record_count':count} for (sid,category,confirmed),count in sorted(classifications.items())],
        'records':records,'issues':issues,'legacy_reading_needs_analysis':legacy,'practice_eligible':False,'publish_payload':None,'database_written':False,
        'counting_note':'数量为分析记录数，不等于独立错题次数、错误率或未掌握程度；未核实记录分别显示，不自动评级或派题。',
        'content_scope':'仅分类及智能体分析；不含原文、原题、题图、答案、学生自述原话或非智能体分析正文。'}

def markdown(report):
    clean=lambda value:str(value).replace('|','／').replace('\r',' ').replace('\n',' ')
    lines=['# 阅读理解分析摘要','',report['content_scope'],report['counting_note'],'',f'本次收录 {report["included_record_count"]} 条；格式待核 {report["quarantined_record_count"]} 条；旧题待补分析 {len(report["legacy_reading_needs_analysis"])} 条。','', '| 科目 | 分类 | 家长核对 | 记录数 |','| --- | --- | --- | ---: |']
    for row in report['classifications']:lines.append(f'| {clean(row["subject_name"])} | {clean(row["reading_category"])} | {"已核对" if row["parent_confirmed"] else "待核对"} | {row["record_count"]} |')
    for r in report['records']:
        lines.extend(['',f'## {clean(r["subject_name"])} · {clean(r["reading_category"])}',f'记录：{r["record_id"]}；日期：{r["recorded_on"]}；状态：{STATUS[r["status"]]}'])
        if r['analysis_text']:lines.extend(['','智能体分析：',r['analysis_text']])
        else:lines.append('仅保留分类，尚无可输出的智能体分析；未核对内容不作为已确认错因。')
        if r['knowledge_points']:lines.append('知识点：'+'、'.join(r['knowledge_points']))
    if report['issues'] or report['legacy_reading_needs_analysis']:
        lines.extend(['','## 待核与待补清单'])
        lines.extend('- '+(i.get('record_id') or '编号异常')+'：'+i['message'] for i in report['issues'])
        lines.extend('- '+i['question_id']+'：'+i['message'] for i in report['legacy_reading_needs_analysis'])
    return '\n'.join(lines)+'\n'

def write_report(out,report):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);stem='reading-summary-'+uuid4().hex[:8];json_path=out/(stem+'.json');md_path=out/(stem+'.md')
    with json_path.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    with md_path.open('x') as f:f.write(markdown(report))
    return {'json':str(json_path.resolve()),'markdown':str(md_path.resolve())}

def main():
    parser=argparse.ArgumentParser(description='只读阅读分析与分类，不读取原题、题图和答案');parser.add_argument('--database',required=True);parser.add_argument('--out',required=True);parser.add_argument('--subject');parser.add_argument('--date-from');parser.add_argument('--date-to');args=parser.parse_args()
    try:
        report=summarize(args.database,args.subject,args.date_from,args.date_to);files=write_report(args.out,report);print(json.dumps({**files,'included_record_count':report['included_record_count'],'quarantined_record_count':report['quarantined_record_count'],'database_written':False},ensure_ascii=False));return 0
    except (ValueError,OSError,sqlite3.Error) as error:print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
