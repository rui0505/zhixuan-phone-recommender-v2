"""Create a model-matched display snapshot from an existing buyer-review CSV."""
import argparse
from collections import Counter
from contextlib import closing
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

FORMAT='taobao-buyer-reviews-v1'
GRADES={'好评':'good','中评':'neutral','差评':'bad','默认好评':'default_good'}

def normalized(value):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',value)).casefold()

def anonymous(value):
    value=re.sub(r'(?<!\d)(?:\+?86[ -]?)?1[3-9]\d{9}(?!\d)','[联系方式已隐藏]',value)
    value=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[邮箱已隐藏]',value)
    value=re.sub(r'(?i)(?:微信|wechat|wx|QQ|电话|手机号|联系)[号号码\s:：]*[a-zA-Z0-9_+-]{5,}', '[联系方式已隐藏]',value)
    return value

def dated(value):
    try:return date.fromisoformat(value[:10]).isoformat()
    except ValueError:return None

def build(source,db):
    source,db=Path(source),Path(db)
    with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as con:
        models={(normalized(brand),normalized(name)):{'model_id':mid,'display_name':name}
                for mid,brand,name in con.execute('SELECT model_id,brand,display_name FROM phone_models')}
    grouped={};seen=set();excluded=Counter();total=0
    with source.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        required={'品牌','型号','商品ID','商品标题','用户名','评价等级','评价时间','评价内容','购买规格','追评内容','采集时间'}
        if not required.issubset(reader.fieldnames or []):raise ValueError('缺少评价导入字段')
        for row in reader:
            total+=1
            model=models.get((normalized(row['品牌']),normalized(row['型号'])))
            if not model:
                excluded['missing_model' if not row['型号'].strip() else 'unmatched_model']+=1;continue
            if not row['评价内容'].strip() and not row['追评内容'].strip():excluded['empty_content']+=1;continue
            identity=[model['model_id'],row['商品ID'],row['用户名'],row['评价时间'],row['评价内容'],row['购买规格'],row['追评内容']]
            ident=hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:24]
            if ident in seen:excluded['duplicate']+=1;continue
            seen.add(ident)
            record={'id':ident,'username':row['用户名'],'grade':GRADES.get(row['评价等级'].strip(),'unknown'),
                    'grade_label':anonymous(row['评价等级'].strip()) or '未标注',
                    'reviewed_at':dated(row['评价时间']), 'collected_at':dated(row['采集时间']),
                    'content':anonymous(row['评价内容']), 'purchase_spec':anonymous(row['购买规格']),
                    'follow_up':anonymous(row['追评内容']), 'product_title':anonymous(row['商品标题']),
                    'product_id':row['商品ID'] if re.fullmatch(r'\d{1,24}',row['商品ID']) else None}
            grouped.setdefault(model['model_id'],[]).append(record)
    for records in grouped.values():records.sort(key=lambda r:(r['reviewed_at'] or '',r['id']),reverse=True)
    return {'format':FORMAT,'snapshot_sha256':hashlib.sha256(db.read_bytes()).hexdigest(),
            'dataset_version':'taobao-csv-'+hashlib.sha256(source.read_bytes()).hexdigest()[:12],
            'source_rows':total,'imported_rows':sum(map(len,grouped.values())),
            'excluded':dict(excluded),'model_count':len(grouped),'models':grouped}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--db',default='data/catalog.sqlite');parser.add_argument('--output',default='data/buyer_reviews.json')
    args=parser.parse_args();data=build(args.source,args.db)
    Path(args.output).write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print(json.dumps({k:v for k,v in data.items() if k!='models'},ensure_ascii=False))
