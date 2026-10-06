"""Same-origin ASGI entrypoint. No Streamlit, user-file access or model calls."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

from catalog.release import ReleaseStore
from .service import CatalogService
from .contracts import (ApiError, parse_recommendation, parse_catalog_query,
                        parse_pagination, parse_compare, parse_export)

ROOT=Path(__file__).resolve().parents[1]
BODY_LIMIT=32768
MIMES={'csv':'text/csv; charset=utf-8','json':'application/json; charset=utf-8',
       'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}


def response_json(value,status=200):
    return Response(json.dumps(value,ensure_ascii=False,allow_nan=False),status_code=status,
                    media_type='application/json',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def error_response(error):
    return response_json({'error':{'code':error.code,'message':error.message}},error.status)


def _unique_object(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError('duplicate key')
        out[key]=value
    return out


async def read_body(request):
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>BODY_LIMIT:
            raise ApiError(413,'body_too_large','请求超过 32 KiB，请减少内容')
        data.extend(chunk)
    try:
        def reject_constant(value):raise ValueError('nonfinite JSON')
        return json.loads(data.decode('utf-8'),object_pairs_hook=_unique_object,parse_constant=reject_constant)
    except (ValueError,UnicodeError,RecursionError) as exc:
        raise ApiError(400,'invalid_json','请求不是有效的 UTF-8 JSON，或含重复键') from exc


def create_app(db_path: Path | None=None, frontend_dir: Path | None=None):
    # Only trusted process configuration injects paths; HTTP parsers reject them.
    store=ReleaseStore(db_path or Path(os.environ.get('PHONE_CATALOG_PATH',ROOT/'data/catalog.sqlite')))
    service=CatalogService(store)

    @asynccontextmanager
    async def lifespan(app):
        try:await run_in_threadpool(store.get)
        except (ValueError,OSError):pass  # alive != ready; each use retries validation
        yield

    def execute(request,body):
        try:store.get()
        except (ValueError,OSError) as exc:
            raise ApiError(503,'release_unavailable','数据发布暂不可用，请联系维护者或稍后重试') from exc
        path=request.url.path; queries=list(request.query_params.multi_items())
        if path=='/readyz':return response_json({'status':'ready','release':service.meta()['release']})
        if path=='/api/v1/meta':return response_json(service.meta())
        if path=='/api/v1/catalog':return response_json(service.catalog(parse_catalog_query(queries,service.meta()['brands'])))
        if 'sku_id' in request.path_params:return response_json(service.details(request.path_params['sku_id']))
        if path=='/api/v1/compare':return response_json(service.compare(parse_compare(body)))
        brands=service.meta()['brands']
        if path=='/api/v1/recommend':
            pagination=parse_pagination(queries); parsed=parse_recommendation(body,brands)
            return response_json(service.recommendation_payload(service.recommend(parsed),pagination))
        if 'format' in request.path_params:
            if queries:raise ApiError(422,'invalid_request','导出不接受查询参数')
            parsed,fps=parse_export(body,brands); fmt=request.path_params['format']
            data=service.export(parsed,fps,fmt)
            return Response(data,headers={'Content-Type':MIMES[fmt],
                'Content-Disposition':f'attachment; filename="zhixuan-recommendation.{fmt}"',
                'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
        raise ApiError(404,'not_found','接口不存在')

    async def endpoint(request):
        try:
            body=await read_body(request) if request.method=='POST' else None
            return await run_in_threadpool(execute,request,body)
        except ApiError as exc:return error_response(exc)
        except Exception:return error_response(ApiError(500,'internal_error','服务内部异常，请稍后重试或联系维护者'))

    async def health(request):return response_json({'status':'alive'})

    async def http_error(request,exc):
        code='not_found' if exc.status_code==404 else 'invalid_request'
        return error_response(ApiError(exc.status_code,code,'资源不存在或请求方法不允许'))

    routes=[Route('/healthz',health),Route('/readyz',endpoint),Route('/api/v1/meta',endpoint),
            Route('/api/v1/catalog',endpoint),Route('/api/v1/skus/{sku_id}',endpoint),
            Route('/api/v1/compare',endpoint,methods=['POST']),Route('/api/v1/recommend',endpoint,methods=['POST']),
            Route('/api/v1/export/{format}',endpoint,methods=['POST'])]
    static=Path(frontend_dir) if frontend_dir is not None else ROOT/'frontend'
    if static.is_dir():routes.append(Mount('/',app=StaticFiles(directory=static,html=True,follow_symlink=False)))
    app=Starlette(routes=routes,lifespan=lifespan,exception_handlers={HTTPException:http_error})
    app.state.service=service
    return app


app=create_app()
