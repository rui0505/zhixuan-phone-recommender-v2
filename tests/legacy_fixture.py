"""Synthetic legacy-schema data, NOT phone specifications or published catalog.

Names exercise brand parsing only. All numbers are artificial. Written only to
a temporary directory; no desktop workbook, credential or data/phones.xlsx used.
"""
import atexit
from pathlib import Path
from tempfile import TemporaryDirectory
from openpyxl import Workbook
from engine import load_data

_TEMP=TemporaryDirectory(prefix='synthetic-legacy-')
atexit.register(_TEMP.cleanup)
_PATH=Path(_TEMP.name)/'synthetic.xlsx'
MODELS=('iPhone 15','Redmi K80','小米 测试A','华为 测试B','荣耀 测试C','三星 测试D','vivo 测试E','OPPO 测试F','一加 测试G')
MODEL_COUNT=len(MODELS);CONFIG_COUNT=MODEL_COUNT*2

def fixture_path():
    if not _PATH.exists():
        wb=Workbook();ws=wb.active
        headers=['产品名称','运行内存','存储','重量','后摄像头','前摄像头','处理器','屏幕大小','分辨率','屏幕刷新率','充电功率','价格']
        ws.append(headers)
        for i,name in enumerate(MODELS):
            for j in range(2):
                storage='1TB (LPDDR5T)' if i==1 and j==1 else ('128GB' if i==0 and j==0 else ('256GB' if j==0 else '512GB'))
                ws.append([name,'8GB' if j==0 else '12GB',storage,f'{180+2*i+j}g','合成50MP','合成16MP','SYNTHETIC',f'{6.1 if i==0 and j==0 else 6.5+j*.2}英寸','合成2000*1000',f'{60+60*j}Hz','合成50W',f'{2000+i*250+j*100}元'])
        wb.save(_PATH);wb.close()
    return _PATH

def load_fixture():
    return load_data(fixture_path())
