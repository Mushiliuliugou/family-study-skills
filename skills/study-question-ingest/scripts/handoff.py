#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Issue an immutable, read-only handoff from the committed ingestion receipt."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4
from ingest import connect,receipt,uid,hashed,stamp,Invalid,require,write_json

def make(database,batch):
    require(uid(batch),'批次编号不正确')
    with closing(connect(database)) as db:
        db.execute('BEGIN');r=receipt(db,batch)
        require(isinstance(r,dict) and r.get('status')=='applied' and r.get('batch_id')==batch,'仅能交接数据库内仍有效的已提交入库批次；预览或已撤销批次不能交接')
        epoch=db.execute("SELECT value FROM settings WHERE id='data_epoch'").fetchone()[0];items=[]
        changes=r.get('changes',[]);require(isinstance(changes,list) and changes and r.get('changed')==len(changes),'入库回执明细不完整')
        for source in changes:
            qid=source['question_id'];q=db.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone()
            require(q and q['subject_id']==source['subject_id'],'原题缺失或已换科目，请先核对入库记录')
            a=json.loads(q['agent_annotations']);entry=next((x for x in a if x.get('batch_id')==batch),None)
            require(entry and entry.get('status')=='unverified' and entry.get('handoff_sha256')==r.get('handoff_sha256'),'此批整理条目缺失或来源不一致')
            ids=entry.get('source_intake_ids',[]);require(isinstance(ids,list) and ids,'缺少原始收题来源')
            for iid in ids:
                row=db.execute('SELECT question_id FROM intake_records WHERE id=?',(iid,)).fetchone();require(row and row[0]==qid,'收题来源无法对应原题')
            state=db.execute('SELECT * FROM review_state WHERE question_id=?',(qid,)).fetchone();require(state is not None,'缺少复习状态')
            items.append({'question_id':qid,'subject_id':q['subject_id'],'title':q['title'],'question_version':q['updated_at'],'source_intake_ids':ids,
                'classification_status':'unverified','priority_status':'not_confirmed','quality_snapshot':q['status'],'review_snapshot':{'mode':state['mode'],'due_date':state['due_date'],'updated_at':state['updated_at']}})
        require(len({i['question_id'] for i in items})==len(items),'交接题号重复')
        identity=hashed({'batch_id':batch,'epoch':epoch,'items':items,'receipt':hashed(r)})
        report={'format':'study-question-ingest-to-review','version':1,'handoff_id':identity,'created_at':stamp(),'data_epoch':epoch,'producer_skill':'study-question-ingest','consumer_skill':'study-question-review',
            'source_batch_id':batch,'source_receipt_sha256':hashed(r),'source_committed_at':r['created_at'],'question_count':len(items),'items':items,'database_written':False,'learning_result_recorded':False}
        report['handoff_sha256']=hashed(report);return report

def main():
    p=argparse.ArgumentParser(description='从真实入库回执生成跨Skill交接文件，不写数据库');p.add_argument('--database',required=True);p.add_argument('--batch-id',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    try:
        r=make(a.database,a.batch_id);path=Path(a.out)/('handoff-'+r['handoff_id'][:12]+'-'+uuid4().hex[:6]+'.json');write_json(path,r);print(json.dumps({'file':str(path),'question_count':r['question_count'],'status':'handoff_ready'},ensure_ascii=False))
    except (Invalid,ValueError,KeyError,TypeError,OSError,sqlite3.Error) as e:
        failed={'format':'study-question-handoff-failure','created_at':stamp(),'status':'blocked','source_batch_id':a.batch_id,'error':str(e),'database_written':False}
        try:write_json(Path(a.out)/('handoff-failed-'+uuid4().hex[:8]+'.json'),failed)
        except OSError:pass
        print(json.dumps(failed,ensure_ascii=False),file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
