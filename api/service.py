"""Read-only, request-local services shared with the canonical ranking core."""
from copy import deepcopy
from dataclasses import dataclass

from catalog.adapter import build_input
from catalog.evidence import build_evidence, render_template, digest
from catalog.read_views import sku_details, sku_sources, safe_url
from catalog.release import ReleaseStore
from catalog.repository import CatalogRepository
from engine import rank_decision_input
from ui.export_results import export_results
from .contracts import ApiError, RecommendationRequest, Pagination, METRICS, DEFAULT_WEIGHTS

CARD_FIELDS = ('sku_id','model_id','variant_id','brand','display_name','variant_name','region',
               'satellite_support','ram_raw','ram_gb','storage_raw','storage_gb','memory_type',
               'price_id','price_cny','currency','price_kind','condition','is_estimated',
               'observed_at','score')


def project(row, fields):
    return {key:row.get(key) for key in fields}


def page_rows(rows, pagination):
    start=(pagination.page-1)*pagination.page_size
    return rows[start:start+pagination.page_size]


def public_evidence(bundle):
    # Preserve core ranking/input bindings. Safe view has its own content digest.
    view=deepcopy(bundle)
    for fact in view['facts'].values():
        for source in fact.get('sources',[]):
            source['url']=safe_url(source.get('url'))
    view['evidence_sha256']=digest({k:v for k,v in view.items() if k!='evidence_sha256'})
    return view


@dataclass(frozen=True)
class RecommendationOutcome:
    di: dict
    result: object
    bundle: dict | None
    release_key: dict[str,str]
    needs: dict


class CatalogService:
    def __init__(self, store: ReleaseStore):
        self.store=store

    def _unchanged(self, key):
        try:
            same=self.store.get().key==key
        except (ValueError,OSError):
            same=False
        if not same:
            raise ApiError(409,'release_changed','数据发布已变化，请刷新并重新确认条件')

    def meta(self):
        release=self.store.get(); repo=release.repo
        brands=sorted(set(r['brand'] for r in repo.as_records(repo.list_models()) if r.get('brand')))
        result={'release':deepcopy(release.key),'counts':{k:int(v) for k,v in repo.summary().items()},
                'brands':brands,'metrics':list(METRICS),'defaults':{'weights':dict(DEFAULT_WEIGHTS),'target_screen':6.5,'mode':'new'},
                'acceptance':deepcopy(release.manifest.get('acceptance',{})),
                'features':{'llm':False,'vikor':False,'images':False,'weights':'试验默认值，非学习所得',
                            'game':'仅限同条件王者荣耀平均帧率；无完整游戏总分'}}
        self._unchanged(release.key); return result

    def catalog(self, query):
        release=self.store.get(); n=query.needs
        rows=release.repo.as_records(release.repo.load_catalog(mode=query.mode,
            min_storage_gb=n['min_storage'],min_price=n['budget']['min'],max_price=n['budget']['max'],
            excluded_brands=n['brands']['exclude']))
        include=n['brands']['include']; search=query.q.casefold()
        rows=[project(r,CARD_FIELDS) for r in rows if (not include or r['brand'] in include)
              and (not search or search in ' '.join(str(r.get(k) or '') for k in
                   ('display_name','brand','variant_name','ram_raw','storage_raw','sku_id')).casefold())]
        rows.sort(key=lambda r:r['sku_id'])
        result={'items':page_rows(rows,query.pagination),'total':len(rows),
                'page':query.pagination.page,'page_size':query.pagination.page_size,'release':deepcopy(release.key)}
        self._unchanged(release.key); return result

    def _details(self, release, sku_id):
        raw=sku_details(release.repo,sku_id)
        if raw is None:raise ApiError(404,'not_found','未找到该配置，请刷新目录')
        spec_fields=('spec_id','subject_type','subject_id','spec_key','unit','value_num','value_text','raw_value','parse_status','comparison_group')
        price_fields=('price_id','sku_id','amount','currency','price_kind','condition','is_estimated','channel','observed_at','source_id')
        run_fields=('run_id','subject_type','subject_id','model_id','variant_id','sku_id','benchmark','institution','protocol','conditions_json','run_date','comparison_group','applicable_scope')
        obs_fields=('observation_id','run_id','metric_key','value_num','unit','missing_reason','comparison_group','verification_status')
        return {'sku':project(raw['sku'],CARD_FIELDS),'model':project(raw['model'],('model_id','brand','display_name','release_month')),
                'specifications':[project(r,spec_fields) for r in raw['specifications']],
                'prices':[project(r,price_fields) for r in raw['prices']],
                'benchmark_runs':[project(r,run_fields) for r in raw['benchmark_runs']],
                'observations':[project(r,obs_fields) for r in raw['observations']],
                'evaluation_note':'机型级评测可见不等于已参与此配置排名；适用性由可比输入规则判定。',
                'sources':sku_sources(release.repo,raw),'release':deepcopy(release.key)}

    def details(self, sku_id):
        release=self.store.get(); result=self._details(release,sku_id)
        self._unchanged(release.key); return result

    def compare(self, sku_ids):
        release=self.store.get(); result={'items':[self._details(release,i) for i in sku_ids],'release':deepcopy(release.key)}
        self._unchanged(release.key); return result

    def recommend(self, request: RecommendationRequest):
        release=self.store.get()
        if request.expected_release!=release.key:
            raise ApiError(409,'release_changed','发布版本不一致，请刷新并重新确认')
        needs=deepcopy(request.needs); weights=deepcopy(request.weights)
        try:
            di=build_input(release.repo,needs,weights,request.target_screen,request.mode)
            result=rank_decision_input(di,target_screen=request.target_screen)
            bundle=build_evidence(di,result,needs) if result.status=='ready' else None
        except ValueError:
            self._unchanged(release.key)
            raise
        self._unchanged(release.key)
        return RecommendationOutcome(di,result,bundle,deepcopy(release.key),needs)

    def recommendation_payload(self, outcome, pagination: Pagination):
        di,result=outcome.di,outcome.result
        ranked=page_rows(CatalogRepository.as_records(result.ranked),pagination)
        def unscored(frame):
            evidence={r['sku_id']:deepcopy(r['criteria']) for r in di['provenance']}
            rows=[]
            for r in CatalogRepository.as_records(frame):
                criteria=evidence[r['sku_id']]
                for criterion in criteria:
                    for source in criterion.get('sources',[]):source['url']=safe_url(source.get('url'))
                rows.append({**project(r,CARD_FIELDS),'criteria':criteria})
            return rows
        explanations={r['sku_id']:render_template(build_evidence(di,result,outcome.needs,sku_ids=[r['sku_id']])) for r in ranked}
        payload={'status':result.status,'reason':result.reason,'ranked':[project(r,CARD_FIELDS) for r in ranked],
                 'ranked_total':len(result.ranked),'page':pagination.page,'page_size':pagination.page_size,
                 'unscored':unscored(result.unscored_candidates),'backups':unscored(result.backups),
                 'coverage':deepcopy(di['coverage_summary']),'selection_rules':deepcopy(di['selection_rules']),
                 'effective_weights':deepcopy(di['weights']),'target_screen':result.decision_context['target_screen'],
                 'dataset_version':result.dataset_version,'feature_rule_version':result.feature_rule_version,
                 'algorithm_version':result.algorithm_version,'decision_sha256':result.decision_sha256,
                 'ranking_sha256':result.ranking_sha256,'release':deepcopy(outcome.release_key),
                 'evidence':public_evidence(outcome.bundle) if outcome.bundle else None,
                 'sources':[], 'explanations':explanations}
        if payload['evidence']:
            payload['sources']=list({s['source_id']:s for fact in payload['evidence']['facts'].values() for s in fact['sources']}.values())
        self._unchanged(outcome.release_key); return payload

    def export(self, request, fingerprints, format):
        if format not in ('csv','xlsx','json'):raise ApiError(422,'invalid_request','只支持 CSV、XLSX、JSON')
        outcome=self.recommend(request); result=outcome.result
        if result.status!='ready':raise ApiError(409,'not_ready','可比数据不足，不能导出推荐成绩')
        if any(getattr(result,k)!=fingerprints[k] for k in ('decision_sha256','ranking_sha256')):
            raise ApiError(409,'decision_changed','本次决策指纹不匹配，请重新生成推荐')
        data=export_results(result,public_evidence(outcome.bundle),format)
        self._unchanged(outcome.release_key); return data
