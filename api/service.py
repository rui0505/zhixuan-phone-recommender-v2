"""Read-only, request-local services shared with the canonical ranking core."""
from copy import deepcopy
from dataclasses import dataclass

from catalog.adapter import build_input
from catalog.default_references import build_reference_input
from catalog.spec_reference import build_spec_reference_input, card_scores, reference_version
from catalog.evidence import build_evidence, render_template, digest
from catalog.read_views import sku_details, sku_sources, safe_url
from catalog.release import ReleaseStore
from catalog.repository import CatalogRepository
from ranking_modes import rank, ALGORITHM_VERSIONS, SCORE_LABELS, HYBRID_PARAMETERS
from ui.export_results import export_results
from .contracts import ApiError, RecommendationRequest, Pagination, METRICS, DEFAULT_WEIGHTS, validate_game_scope, validate_defaulted_metrics, validate_evaluation_mode
from .categories import CATEGORY_LABELS, CATEGORY_RULE_VERSION, model_categories
from .presentation import recommendation_reason
from .performance import performance_capabilities, BATTERY_METRIC

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
    performance_capabilities: dict


class CatalogService:
    def __init__(self, store: ReleaseStore, images=None, explanations=None):
        self.store=store
        self.images=images; self.explanations=explanations
        self._category_cache=None
        self._performance_cache=None

    def _category_index(self,release):
        cached=self._category_cache
        if cached is not None and cached[0]==release.key:
            return cached[1]
        index=model_categories(release.repo)
        self._category_cache=(deepcopy(release.key),index)
        return index

    @staticmethod
    def _category_counts(index,model_ids=None):
        ids=set(index) if model_ids is None else set(model_ids).intersection(index)
        return [{'id':key,'label':label,'rule_version':CATEGORY_RULE_VERSION,'model_count':len(ids),
                 'evidence_available':sum(any(c['id']==key and c['status']=='evidence_available' for c in index[m]) for m in ids),
                 'pending_review':sum(any(c['id']==key and c['status']=='pending_review' for c in index[m]) for m in ids)}
                for key,label in CATEGORY_LABELS.items()]

    def card(self,row,release_key,category_index=None):
        result=project(row,CARD_FIELDS)
        result['image']=self.images.for_model(row.get('model_id')) if self.images and self.images.matches(release_key['snapshot_sha256']) else None
        if category_index is not None:
            result['category_assessments']=deepcopy(category_index.get(row.get('model_id'),[]))
        return result

    def _unchanged(self, key):
        try:
            same=self.store.get().key==key
        except (ValueError,OSError):
            same=False
        if not same:
            raise ApiError(409,'release_changed','数据发布已变化，请刷新并重新确认条件')

    def meta(self):
        release=self.store.get(); repo=release.repo
        image_count=self.images.count if self.images and self.images.matches(release.key['snapshot_sha256']) else 0
        brands=sorted(set(r['brand'] for r in repo.as_records(repo.list_models()) if r.get('brand')))
        if self._performance_cache is None or self._performance_cache[0]!=release.key:
            needs={'budget':{'min':None,'max':None},'min_storage':None,'brands':{'include':[],'exclude':[]}}
            self._performance_cache=(deepcopy(release.key),performance_capabilities(repo,needs,'new'))
        result={'release':deepcopy(release.key),'counts':{k:int(v) for k,v in repo.summary().items()},
                'algorithms':[{'method':method,'algorithm_version':version,'score_label':SCORE_LABELS[method],
                               'experimental':method=='hybrid_rrf',
                               'parameters':deepcopy(HYBRID_PARAMETERS) if method=='hybrid_rrf' else {}}
                              for method,version in ALGORITHM_VERSIONS.items()],
                'performance_capabilities':deepcopy(self._performance_cache[1]),
                'reference_rule_version':reference_version(repo),
                'brands':brands,'metrics':list(METRICS),'defaults':{'weights':dict(DEFAULT_WEIGHTS),'defaulted_metrics':list(METRICS),'target_screen':6.5,'mode':'new','evaluation_mode':'spec_reference'},
                'categories':self._category_counts(self._category_index(release)),
                'acceptance':deepcopy(release.manifest.get('acceptance',{})),
                'features':{'llm':bool(self.explanations and self.explanations.available),'vikor':False,'images':bool(image_count),'image_models':image_count,'weights':'试验默认值，非学习所得',
                            'game':'仅限同条件王者荣耀平均帧率；无完整游戏总分'}}
        self._unchanged(release.key); return result

    def catalog(self, query):
        release=self.store.get(); n=query.needs
        rows=release.repo.as_records(release.repo.load_catalog(mode=query.mode,
            min_storage_gb=n['min_storage'],min_price=n['budget']['min'],max_price=n['budget']['max'],
            excluded_brands=n['brands']['exclude']))
        include=n['brands']['include']; search=query.q.casefold()
        index=self._category_index(release)
        rows=[self.card(r,release.key,index) for r in rows if (not include or r['brand'] in include)
              and (not search or search in ' '.join(str(r.get(k) or '') for k in
                   ('display_name','brand','variant_name','ram_raw','storage_raw','sku_id')).casefold())]
        counts=self._category_counts(index,[r['model_id'] for r in rows])
        if query.category!='all':
            rows=[r for r in rows if any(c['id']==query.category and c['status']==query.category_status
                                         for c in r['category_assessments'])]
        rows.sort(key=lambda r:r['sku_id'])
        notice=('场景分类按现有资料整理，不代表性能认证；同一机型可以有多个标签。'
                if query.category=='all' else
                '当前尚缺少已核实的简易模式、大字、通话和紧急联系等适老资料。待评估不等于不适合，可切换资料状态查看。'
                if query.category=='senior' else
                '当前展示待评估机型；缺少资料不等于表现不好，不能用芯片、像素或价格推断体验。'
                if query.category_status=='pending_review' else
                '有游戏评测资料，可按原测试条件了解表现；这不是全游戏性能认证，具体配置也未独立实测。'
                if query.category=='gaming' else
                '有照片评测资料，可查看各自的协议和优势场景；不同机构或协议的成绩不混合比较。')
        result={'items':page_rows(rows,query.pagination),'total':len(rows),
                'page':query.pagination.page,'page_size':query.pagination.page_size,'release':deepcopy(release.key),
                'category':query.category,'category_status':query.category_status,'category_counts':counts,'category_notice':notice}
        self._unchanged(release.key); return result

    def _details(self, release, sku_id):
        raw=sku_details(release.repo,sku_id)
        if raw is None:raise ApiError(404,'not_found','未找到该配置，请刷新目录')
        spec_fields=('spec_id','subject_type','subject_id','spec_key','unit','value_num','value_text','raw_value','parse_status','comparison_group')
        price_fields=('price_id','sku_id','amount','currency','price_kind','condition','is_estimated','channel','observed_at','source_id')
        run_fields=('run_id','subject_type','subject_id','model_id','variant_id','sku_id','benchmark','institution','protocol','conditions_json','run_date','comparison_group','applicable_scope')
        obs_fields=('observation_id','run_id','metric_key','value_num','unit','missing_reason','comparison_group','verification_status')
        return {'sku':self.card(raw['sku'],release.key,self._category_index(release)),'model':project(raw['model'],('model_id','brand','display_name','release_month')),
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
        if type(request.method) is not str or request.method not in ALGORITHM_VERSIONS:
            raise ApiError(422,'invalid_request','请选择有效排序方法')
        if request.expected_algorithm_version is not None and request.expected_algorithm_version!=ALGORITHM_VERSIONS[request.method]:
            raise ApiError(409,'algorithm_changed','算法版本已变化，请刷新并重新确认')
        needs=deepcopy(request.needs); weights=deepcopy(request.weights)
        if request.method == 'hybrid_rrf' and any(weights.get(k,0)>0 for k in ('price_cny','refresh_rate_hz')):
            raise ApiError(422,'invalid_request','混合排序的价格和品牌仅作为硬性筛选条件')
        defaults=validate_defaulted_metrics(request.defaulted_metrics,weights)
        evaluation_mode=validate_evaluation_mode(request.evaluation_mode)
        if request.expected_reference_rule_version is not None and request.expected_reference_rule_version!=reference_version(release.repo):
            raise ApiError(409,'reference_rule_changed','参考评分规则已变化，请刷新后重新确认')
        validate_game_scope(request.game_scope, {} if evaluation_mode=='spec_reference' else {k:v for k,v in weights.items() if k not in defaults})
        if weights.pop(BATTERY_METRIC,0)>0:
            raise ApiError(422,'unsupported_metric','当前发布不支持续航实测排序')
        try:
            di=(build_spec_reference_input(release.repo,needs,weights,request.target_screen,request.mode)
                if evaluation_mode=='spec_reference' else
                build_reference_input(release.repo,needs,weights,request.target_screen,request.mode,defaults,request.game_scope))
            if evaluation_mode=='spec_reference':di['selection_rules']['requested_game_scope']=request.game_scope
            if evaluation_mode=='spec_reference' and request.expected_reference_rule_version is not None and request.expected_reference_rule_version!=di['selection_rules']['reference_scoring']['version']:
                raise ApiError(409,'reference_rule_changed','参考评分规则已变化，请刷新后重新确认')
            if request.game_scope != 'honor_of_kings':
                di['selection_rules']['game_scope'] = request.game_scope
            result=rank(di,target_screen=request.target_screen,method=request.method)
            bundle=build_evidence(di,result,needs) if result.status=='ready' else None
            capabilities=performance_capabilities(release.repo,needs,request.mode)
        except ValueError:
            self._unchanged(release.key)
            raise
        self._unchanged(release.key)
        return RecommendationOutcome(di,result,bundle,deepcopy(release.key),needs,capabilities)

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
        reasons={r['sku_id']:recommendation_reason(outcome.bundle,r['sku_id']) for r in ranked}
        payload={'status':result.status,'reason':result.reason,'ranked':[self.card(r,outcome.release_key) for r in ranked],
                 'evaluation_mode':di['selection_rules'].get('evaluation_mode','measured'),
                 'reference_rule_version':di['selection_rules'].get('reference_scoring',{}).get('version') or reference_version(self.store.get().repo),
                 'ranked_total':len(result.ranked),'page':pagination.page,'page_size':pagination.page_size,
                 'unscored':unscored(result.unscored_candidates),'backups':unscored(result.backups),
                 'coverage':deepcopy(di['coverage_summary']),'selection_rules':deepcopy(di['selection_rules']),
                 'performance_capabilities':deepcopy(outcome.performance_capabilities),
                 'effective_weights':deepcopy(di['weights']),'target_screen':result.decision_context['target_screen'],
                 'dataset_version':result.dataset_version,'feature_rule_version':result.feature_rule_version,
                 'algorithm_version':result.algorithm_version,'decision_sha256':result.decision_sha256,
                 'method':result.decision_context.get('method','topsis'),
                 'score_label':SCORE_LABELS[result.decision_context.get('method','topsis')],
                 'ranking_sha256':result.ranking_sha256,'release':deepcopy(outcome.release_key),
                 'evidence':public_evidence(outcome.bundle) if outcome.bundle else None,
                 'sources':[], 'explanations':explanations,'recommendation_reasons':reasons}
        if payload['evaluation_mode']=='spec_reference':
            for card in payload['ranked']:card['reference_scores']=card_scores(di,card['sku_id'])
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
