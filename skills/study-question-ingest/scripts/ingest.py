#!/usr/bin/env python3
"""Standard-library bridge: inspect -> JSON proposal -> plan -> transactional append -> rollback."""
import argparse
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import sys
from uuid import uuid4
import zipfile

SUBJECTS={'chinese','math','english','history','geography','biology','ethics','physics','chemistry'}
REASONS={'concept','reading','method','calculation','expression','unfamiliar','unsure'}
LIMIT=512*1024**2
KEY='study_ingest:'
FIELDS={'title_suggestion','chapter_suggestion','knowledge_points','question_type','reason_hypotheses','parent_check_notes','evidence'}
class Invalid(ValueError):pass

def require(test,message):
    if not test:raise Invalid(message)
def canonical(obj):return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def hashed(obj):return hashlib.sha256(canonical(obj).encode()).hexdigest()
def sha_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def uid(s):return isinstance(s,str) and re.fullmatch('[a-f0-9]{32}',s) is not None
def digest(s):return isinstance(s,str) and re.fullmatch('[a-f0-9]{64}',s) is not None
def text(s,n):return isinstance(s,str) and len(s)<=n
def stamp():return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='microseconds')
def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    # Reports are immutable artifacts; do not replace an earlier review or receipt.
    with path.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
def timestamp(s):
    require(text(s,64),'时间字段不正确')
    try:require(datetime.fromisoformat(s).tzinfo is not None,'时间必须保留时区')
    except (ValueError,TypeError):raise Invalid('时间字段不正确')
def safe_name(name):
    return isinstance(name,str) and bool(name) and '\\' not in name and not PurePosixPath(name).is_absolute() and all(p not in ('..','.') for p in name.split('/'))

def bundle(path):
    path=Path(path);require(path.is_file() and path.stat().st_size<=LIMIT,'交接包不存在或超过512MB，请减少导出批量')
    questions={};intakes={};media={}
    with zipfile.ZipFile(path) as z:
        infos=z.infolist();names=[i.filename for i in infos]
        require(len(names)==len(set(names)) and len(names)<=2000 and all(safe_name(n) for n in names),'ZIP存在重复或不安全路径')
        require(sum(i.file_size for i in infos)<=LIMIT and all(not stat.S_ISLNK(i.external_attr>>16) and not i.is_dir() for i in infos),'ZIP过大或含非常规文件')
        def read_json(name):
            require(name in names and z.getinfo(name).file_size<=8*1024**2,'JSON文件缺失或过大：'+name)
            return json.loads(z.read(name))
        m=read_json('manifest.json')
        require(isinstance(m,dict) and m.get('format')=='family-study-intake-handoff' and m.get('version')==1,'不支持的交接格式')
        require(isinstance(m.get('subjects'),list) and 1<=len(m['subjects'])<=9 and isinstance(m.get('media'),list),'分科索引不正确')
        expected={'manifest.json','交接说明.md'};seen_subjects=set()
        for item in m['media']:
            require(isinstance(item,dict) and re.fullmatch(r'media/[a-f0-9]{32}\.(png|jpg|jpeg)',item.get('file','')) and digest(item.get('sha256')),'图片清单不正确')
            name=item['file'];aid=PurePosixPath(name).stem
            require(name in names and aid not in media,'图片缺失或编号重复')
            with z.open(name) as f:
                h=hashlib.sha256()
                for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
            require(h.hexdigest()==item['sha256'],'图片校验失败：'+name)
            media[aid]={'file':name,'sha256':item['sha256']};expected.add(name)
        for group in m['subjects']:
            require(isinstance(group,dict),'学科索引不正确');sid=group.get('subject_id')
            require(sid in SUBJECTS and sid not in seen_subjects and group.get('file')=='subjects/'+sid+'.json','学科编号或分科文件不正确')
            seen_subjects.add(sid);expected.add(group['file']);d=read_json(group['file'])
            require(isinstance(d,dict) and d.get('subject_id')==sid and isinstance(d.get('questions'),list) and isinstance(d.get('intakes'),list),'分科内容不正确')
            require(group.get('question_count')==len(d['questions']) and group.get('intake_count')==len(d['intakes']),'分科数量不符')
            local_q=set()
            for q in d['questions']:
                require(isinstance(q,dict) and uid(q.get('id')) and q.get('subject_id')==sid and q['id'] not in questions,'题号重复或跨科关联不正确')
                timestamp(q.get('updated_at'));images=q.get('images')
                require(isinstance(images,list) and 1<=len(images)<=16 and any(i.get('role')=='prompt' for i in images if isinstance(i,dict)),'题干图片不完整')
                for i in images:
                    require(isinstance(i,dict) and i.get('role') in ('prompt','figure','work','answer','analysis'),'图片角色不正确')
                    for key,pkey in [('asset_id','asset_path'),('original_id','original_path')]:
                        aid=i.get(key);require(aid in media and i.get(pkey)==media[aid]['file'],'图片路径与统一编号不符')
                questions[q['id']]=q;local_q.add(q['id'])
            touched=set()
            for r in d['intakes']:
                require(isinstance(r,dict) and uid(r.get('id')) and r['id'] not in intakes and r.get('question_id') in local_q and r.get('subject_id')==sid,'收题编号或题目关联不正确')
                timestamp(r.get('created_at'));codes=r.get('reason_codes');origin=r.get('origin','student')
                require(origin in ('parent','student') and (origin!='parent' or (codes==[] and r.get('reason_note')=='')),'收题来源不正确，家长录入不得伪造学生自述')
                require(isinstance(codes,list) and all(isinstance(c,str) and c in REASONS for c in codes) and len(codes)==len(set(codes)) and text(r.get('reason_note'),120),'学生自述字段不正确')
                intakes[r['id']]=r;touched.add(r['question_id'])
            require(touched==local_q,'存在没有收题来源的题目')
        if 'standard-template.json' in names:
            template=read_json('standard-template.json')
            require(isinstance(template,dict) and template.get('format')=='family-study-standard' and template.get('version')==1 and template.get('data_epoch')==m.get('data_epoch') and isinstance(template.get('questions'),list),'标准模板身份或格式不正确')
            template_rows=template['questions']
            require(len(template_rows)==len(questions) and all(isinstance(r,dict) and isinstance(r.get('question_id'),str) for r in template_rows),'标准模板题目数量不正确')
            require({r['question_id'] for r in template_rows}==set(questions),'标准模板题号与原料不符')
            require(all(r.get('subject_id')==questions[r['question_id']]['subject_id'] and r.get('expected_updated_at')==questions[r['question_id']]['updated_at'] for r in template_rows),'标准模板科目或来源版本不符')
            expected.add('standard-template.json')
        require(set(names)==expected,'ZIP包含未登记文件或缺少交接说明')
        require(m.get('question_count')==len(questions) and m.get('intake_count')==len(intakes) and len(intakes)<=500,'总数量不符或超过500次收题')
    return {'sha256':sha_file(path),'manifest':m,'questions':questions,'intakes':intakes,'media':media}

def blank_annotation():return {'title_suggestion':'','chapter_suggestion':'','knowledge_points':[],'question_type':'','reason_hypotheses':[],'parent_check_notes':[],'evidence':[]}
def inspect(path,out):
    b=bundle(path);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    proposal={'format':'study-question-enrichment','version':1,'batch_id':uuid4().hex,'agent_name':'待填写实际智能体名称','handoff_sha256':b['sha256'],'items':[]}
    for q in b['questions'].values():
        proposal['items'].append({'question_id':q['id'],'subject_id':q['subject_id'],'source_updated_at':q['updated_at'],
            'source_intake_ids':sorted(r['id'] for r in b['intakes'].values() if r['question_id']==q['id']),'annotation':blank_annotation()})
    write_json(out/'proposal.json',proposal)
    write_json(out/'source-index.json',{'handoff_sha256':b['sha256'],'subjects':b['manifest']['subjects'],'questions':list(b['questions'].values()),'student_self_reports':[r for r in b['intakes'].values() if r.get('origin','student')=='student'],'intake_records':list(b['intakes'].values())})
    # Only validated image members are extracted, so the agent can actually inspect them.
    with zipfile.ZipFile(path) as z:
        if 'standard-template.json' in z.namelist():write_json(out/'standard-template.json',json.loads(z.read('standard-template.json')))
        for a in b['media'].values():
            dest=out/a['file'];dest.parent.mkdir(exist_ok=True)
            with dest.open('xb') as f:f.write(z.read(a['file']))
    return {'status':'inspected','questions':len(b['questions']),'student_intakes':sum(r.get('origin','student')=='student' for r in b['intakes'].values()),'parent_intakes':sum(r.get('origin')=='parent' for r in b['intakes'].values()),'directory':str(out),'proposal':str(out/'proposal.json')}

def proposal(path,b):
    path=Path(path);require(path.is_file() and path.stat().st_size<=4*1024**2,'整理提案不存在或过大');p=json.loads(path.read_text())
    require(isinstance(p,dict) and set(p)=={'format','version','batch_id','agent_name','handoff_sha256','items'} and p['format']=='study-question-enrichment' and p['version']==1,'整理提案格式不正确')
    require(uid(p['batch_id']) and text(p['agent_name'],80) and p['agent_name'].strip() and p['agent_name']!='待填写实际智能体名称' and p['handoff_sha256']==b['sha256'],'批次、智能体名称或交接包校验值不正确')
    require(isinstance(p['items'],list) and 1<=len(p['items'])<=100,'每批请整理1—100道题');seen=set()
    for row in p['items']:
        require(isinstance(row,dict) and set(row)=={'question_id','subject_id','source_updated_at','source_intake_ids','annotation'},'提案题目字段不正确')
        qid=row['question_id'];require(isinstance(qid,str) and qid in b['questions'] and qid not in seen,'提案题号不存在或重复');seen.add(qid);q=b['questions'][qid]
        require(row['subject_id']==q['subject_id'] and row['source_updated_at']==q['updated_at'],'提案不得改写来源版本或跨科移动题目')
        ids=row['source_intake_ids'];valid={r['id'] for r in b['intakes'].values() if r['question_id']==qid}
        require(isinstance(ids,list) and ids and all(isinstance(i,str) and i in valid for i in ids) and len(ids)==len(set(ids)),'提案收题来源不正确')
        a=row['annotation'];require(isinstance(a,dict) and set(a)==FIELDS,'智能体只可提交约定的待核整理字段')
        for key,n in [('title_suggestion',120),('chapter_suggestion',120),('question_type',80)]:require(text(a[key],n),'整理文本超长或格式不正确')
        for key,n in [('knowledge_points',80),('parent_check_notes',300)]:
            require(isinstance(a[key],list) and len(a[key])<=10 and all(text(x,n) and x.strip() for x in a[key]),'知识点或待核事项不正确')
        paths={i['asset_path'] for i in q['images']}|{i['original_path'] for i in q['images']}
        require(isinstance(a['evidence'],list) and len(a['evidence'])<=32 and all(isinstance(x,str) and x in paths for x in a['evidence']),'证据必须引用本题图像')
        require(isinstance(a['reason_hypotheses'],list) and len(a['reason_hypotheses'])<=7,'错因推测格式不正确')
        for r in a['reason_hypotheses']:
            require(isinstance(r,dict) and set(r)=={'code','note'} and r['code'] in REASONS and text(r['note'],300) and r['note'].strip(),'错因推测必须含理由，不得伪装为家长确认')
        require(not any(a[k] for k in FIELDS-{'evidence'}) or bool(a['evidence']),'非空整理结果需要本题图像证据')
    return p

def connect(path,write=False):
    path=Path(path).resolve();require(path.is_file(),'目标数据库不存在；不能另建空主库')
    # A closed WAL database may need transient SQLite sidecars. query_only enforces
    # read-only SQL while allowing SQLite to open those coordination files.
    db=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=10);db.row_factory=sqlite3.Row
    if not write:db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA trusted_schema=OFF')
    require(db.execute('PRAGMA user_version').fetchone()[0]==4,'目前只支持家庭学习工作台schema=4数据库（应用功能版本另见兼容说明）')
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    require({'questions','settings','assets','intake_records','review_state','review_log'}<=tables,'目标不是兼容的统一题库')
    require({'reason_codes','reason_note'}<={r['name'] for r in db.execute('PRAGMA table_info(intake_records)')},'目标库尚未支持可选错因，请先用新版工作台完成兼容升级')
    require(not db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger'").fetchone(),'目标数据库含非预期触发器，停止写入')
    return db

def annotations(row):
    value=json.loads(row.get('agent_annotations','[]'))
    require(isinstance(value,list) and len(value)<=100 and all(isinstance(x,dict) for x in value),'既有智能体整理字段格式不正确')
    return value

def question_guard(row):return hashed({k:v for k,v in row.items() if k!='updated_at'})
def guard_matches(row,expected):
    if question_guard(row)==expected:return True
    legacy=dict(row)
    for key,default in [('question_kind','unclassified'),('reference_material','{}')]:
        if legacy.get(key)==default:legacy.pop(key,None)
    return question_guard(legacy)==expected
def receipt(db,batch):
    r=db.execute('SELECT value FROM settings WHERE id=?',(KEY+batch,)).fetchone()
    return json.loads(r[0]) if r else None

def prepare(db,b,p):
    old=receipt(db,p['batch_id']);fingerprint=hashed(p)
    if old:
        require(old.get('proposal_sha256')==fingerprint,'此批次编号已用于另一份提案')
        require(old.get('status')=='applied','此批次已经撤销；请重新导出并使用新批次')
        return {'status':'already_applied','batch_id':p['batch_id'],'changed':0,'receipt':old},[]
    changes=[];skip=[];subjects={r[0] for r in db.execute('SELECT id FROM subjects')}
    for item in p['items']:
        qid=item['question_id'];row=db.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone();require(row is not None,'原题不存在；交接包不能当作新题批量新增')
        row=dict(row);q=b['questions'][qid]
        require(row.get('question_kind','unclassified')!='reading_comprehension','阅读理解只接收分析与分类，不接收原题交接')
        require(row.get('question_kind','unclassified')==q.get('question_kind','unclassified'),'录入题型有变化，请重新导出')
        require(json.loads(row.get('reference_material','{}'))==q.get('reference_material',{}),'参考资料有变化，请重新导出')
        require(item['subject_id'] in subjects and row['subject_id']==item['subject_id'],'目标题号的学科不一致')
        require(row['updated_at']==item['source_updated_at'],'原题已更新，请重新导出后整理：'+qid)
        for key in ['title','chapter','source','source_date','note','status','created_at','intake_origin']:
            require(row.get(key)==q.get(key),'交接包与正式原题字段不一致：'+key)
        require(json.loads(row['source_ref'])==q.get('source_ref',{}),'原资料定位有变化')
        images=[{k:i[k] for k in ('asset_id','original_id','role')} for i in q['images']]
        require(json.loads(row['images'])==images,'原题题图有变化')
        for pic in images:
            for key in ['asset_id','original_id']:
                asset=db.execute('SELECT sha256,filename FROM assets WHERE id=?',(pic[key],)).fetchone();source=b['media'][pic[key]]
                require(asset and asset['sha256']==source['sha256'] and 'media/'+asset['filename']==source['file'],'图片与目标题库不符')
                file=Path(db.execute('PRAGMA database_list').fetchone()[2]).parent/'media'/asset['filename']
                require(file.is_file() and sha_file(file)==source['sha256'],'目标题库的实际图片缺失或已变化')
        for iid in item['source_intake_ids']:
            r=db.execute('SELECT * FROM intake_records WHERE id=?',(iid,)).fetchone();source=b['intakes'][iid]
            require(r is not None and r['question_id']==qid and r['origin']==source.get('origin','student'),'收题来源不在目标库或学生/家长来源不一致')
            require(json.loads(r['reason_codes'])==source['reason_codes'] and r['reason_note']==source['reason_note'] and r['created_at']==source['created_at'],'学生原始自述不一致；不允许由智能体改写')
        current=annotations(row);a=item['annotation']
        if not any(a[k] for k in FIELDS-{'evidence'}):skip.append({'question_id':qid,'reason':'没有提供整理结果'});continue
        content_hash=hashed({'source_intake_ids':sorted(item['source_intake_ids']),'annotation':a})
        if any(r.get('content_sha256')==content_hash for r in current):skip.append({'question_id':qid,'reason':'相同整理结果已存在'});continue
        require(len(current)<100,'本题整理记录已达100条，请先检查历史')
        changes.append({'question_id':qid,'subject_id':item['subject_id'],'before':row,'before_annotations':current,'content_sha256':content_hash,'item':item})
    return {'status':'preview','batch_id':p['batch_id'],'proposal_sha256':fingerprint,'changed':len(changes),'skipped':skip,
        'by_subject':{sid:sum(c['subject_id']==sid for c in changes) for sid in sorted({c['subject_id'] for c in changes})},
        'changes':[{'question_id':c['question_id'],'subject_id':c['subject_id'],'title':c['before']['title'],'annotation':c['item']['annotation'],'status':'unverified'} for c in changes],
        'preserved':['原题与图片','学生自述','家长备注与核实状态','学习任务与复习历史']},changes

def plan(path,proposal_path,database):
    b=bundle(path);p=proposal(proposal_path,b)
    with closing(connect(database)) as db:return prepare(db,b,p)[0]

def permit(db,allow_formal):
    row=db.execute("SELECT value FROM settings WHERE id='test_database'").fetchone()
    require(allow_formal or (row and row[0]=='true'),'正式库需要明确授权后使用--allow-formal；目前只读预览，不写入')

def snapshot(db_path,out,batch):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);dest=out/('before-'+batch+'-'+uuid4().hex[:8]+'.sqlite3')
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(dest)) as target:source.backup(target)
    return {'file':str(dest.resolve()),'sha256':sha_file(dest),'coverage':'数据库快照；本操作不改图片或登记原文件，不替代工作台完整ZIP备份'}

def apply(path,proposal_path,database,out,allow_formal=False):
    b=bundle(path);p=proposal(proposal_path,b)
    with closing(connect(database,True)) as db:
        try:
            db.execute('BEGIN IMMEDIATE');permit(db,allow_formal);report,changes=prepare(db,b,p)
            if report['status']=='already_applied' or not changes:db.rollback();return report
            safety=snapshot(database,out,p['batch_id'])
            if 'agent_annotations' not in {r['name'] for r in db.execute('PRAGMA table_info(questions)')}:db.execute("ALTER TABLE questions ADD COLUMN agent_annotations TEXT NOT NULL DEFAULT '[]'")
            rows=[];time=stamp()
            for c in changes:
                qid=c['question_id'];entry={'version':1,'batch_id':p['batch_id'],'agent_name':p['agent_name'],'created_at':time,'handoff_sha256':b['sha256'],
                    'source_intake_ids':c['item']['source_intake_ids'],'content_sha256':c['content_sha256'],'status':'unverified','annotation':c['item']['annotation']}
                after=c['before_annotations']+[entry];db.execute('UPDATE questions SET agent_annotations=?,updated_at=? WHERE id=?',(canonical(after),time,qid))
                row=dict(db.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone())
                rows.append({'question_id':qid,'before_annotations':c['before_annotations'],'after_guard':question_guard(row)})
            result={**report,'status':'applied','created_at':time,'agent_name':p['agent_name'],'handoff_sha256':b['sha256'],'safety_snapshot':safety,'rollback_rows':rows}
            db.execute('INSERT INTO settings VALUES (?,?)',(KEY+p['batch_id'],canonical(result)));db.commit();return result
        except Exception:db.rollback();raise

def rollback(database,batch,out,allow_formal=False):
    require(uid(batch),'批次编号不正确')
    with closing(connect(database,True)) as db:
        try:
            db.execute('BEGIN IMMEDIATE');permit(db,allow_formal);r=receipt(db,batch)
            require(r and r.get('status')=='applied','没有可撤销的已写入批次')
            for item in r['rollback_rows']:
                require(not db.execute("SELECT 1 FROM settings WHERE id LIKE ? LIMIT 1",('agent_decision:'+item['question_id']+':'+batch+':%',)).fetchone(),'本批次已有家长审核，请先在审核页撤销对应审核，再核对入库撤销')
                q=db.execute('SELECT * FROM questions WHERE id=?',(item['question_id'],)).fetchone()
                require(q is not None and guard_matches(dict(q),item['after_guard']),'原题或整理结果之后已变化，不能整批覆盖撤销')
            safety=snapshot(database,out,batch);time=stamp()
            for item in r['rollback_rows']:db.execute('UPDATE questions SET agent_annotations=?,updated_at=? WHERE id=?',(canonical(item['before_annotations']),time,item['question_id']))
            r.update(status='rolled_back',rolled_back_at=time,rollback_snapshot=safety);db.execute('UPDATE settings SET value=? WHERE id=?',(canonical(r),KEY+batch));db.commit();return r
        except Exception:db.rollback();raise

def main():
    parser=argparse.ArgumentParser(description='错题交接包核验、待核整理入库与按批撤销（Python标准库）');sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('inspect');p.add_argument('--handoff',required=True);p.add_argument('--out',required=True)
    for action in ['plan','apply']:
        p=sub.add_parser(action);p.add_argument('--handoff',required=True);p.add_argument('--proposal',required=True);p.add_argument('--database',required=True);p.add_argument('--out',required=True)
        if action=='apply':p.add_argument('--commit',action='store_true',required=True);p.add_argument('--allow-formal',action='store_true')
    p=sub.add_parser('rollback');p.add_argument('--database',required=True);p.add_argument('--batch-id',required=True);p.add_argument('--out',required=True);p.add_argument('--commit',action='store_true',required=True);p.add_argument('--allow-formal',action='store_true')
    args=parser.parse_args()
    try:
        if args.command=='inspect':result=inspect(args.handoff,args.out)
        elif args.command=='plan':result=plan(args.handoff,args.proposal,args.database)
        elif args.command=='apply':result=apply(args.handoff,args.proposal,args.database,args.out,args.allow_formal)
        else:result=rollback(args.database,args.batch_id,args.out,args.allow_formal)
        if args.command!='inspect':write_json(Path(args.out)/(args.command+'-'+uuid4().hex[:8]+'.json'),result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (Invalid,ValueError,KeyError,TypeError,OSError,sqlite3.Error,zipfile.BadZipFile) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
