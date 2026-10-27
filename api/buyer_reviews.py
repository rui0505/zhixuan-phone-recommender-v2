"""Read-only buyer-review samples, kept separate from ranking and catalog data."""
from collections import Counter
import json
from pathlib import Path

from .contracts import ApiError,parse_pagination

GRADES=('all','good','neutral','bad','default_good','unknown')
PUBLIC_FIELDS={'id','username','grade','grade_label','reviewed_at','collected_at','content','purchase_spec','follow_up','product_title','product_id'}

def parse_query(items):
    query={}
    for key,value in items:
        if key not in ('page','page_size','grade') or key in query:
            raise ApiError(422,'invalid_request','评价筛选参数无效')
        query[key]=value
    grade=query.pop('grade','all')
    if grade not in GRADES:raise ApiError(422,'invalid_request','请选择有效的评价等级')
    pagination=parse_pagination(list(query.items()))
    return pagination,grade

class BuyerReviews:
    def __init__(self,path):self.path=Path(path);self._key=None;self._data=None

    def read(self,snapshot):
        if not self.path.is_file():return None
        try:
            stat=self.path.stat();key=(stat.st_mtime_ns,stat.st_size)
            if key!=self._key:
                data=json.loads(self.path.read_text(encoding='utf-8'))
                if data['format']!='taobao-buyer-reviews-v1' or not isinstance(data['models'],dict):raise ValueError()
                for records in data['models'].values():
                    if not isinstance(records,list):raise ValueError()
                    for record in records:
                        if set(record)!=PUBLIC_FIELDS or record['grade'] not in GRADES[1:]:raise ValueError()
                self._data=data;self._key=key
            if self._data['snapshot_sha256']!=snapshot:raise ValueError()
            return self._data
        except (OSError,ValueError,KeyError,TypeError):
            raise ApiError(503,'reviews_unavailable','评价资料暂不可用，请稍后重试') from None

    def for_model(self,model,snapshot,pagination,grade):
        data=self.read(snapshot)
        records=(data or {}).get('models',{}).get(model['model_id'],[])
        counts=Counter(r['grade'] for r in records)
        selected=records if grade=='all' else [r for r in records if r['grade']==grade]
        start=(pagination.page-1)*pagination.page_size
        return {'model':{'model_id':model['model_id'],'display_name':model['display_name']},
                'scope':'model','available':data is not None,'dataset_version':(data or {}).get('dataset_version'),
                'sample_total':len(records),'grade_counts':{g:counts[g] for g in GRADES[1:]},
                'grade':grade,'page':pagination.page,'page_size':pagination.page_size,'total':len(selected),
                'items':selected[start:start+pagination.page_size]}
