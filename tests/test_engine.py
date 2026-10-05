import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
import pandas as pd
from engine import ROW_ID_LEGACY_PREFIX, load_data, rank, stable_id

class RankingTests(unittest.TestCase):
    def setUp(self): self.df=load_data()
    def test_data(self):
        self.assertEqual(len(self.df),156)
        self.assertEqual(self.df['产品名称'].nunique(),46)
        self.assertFalse(self.df[['price','storage','weight','screen','refresh']].isna().any().any())
        self.assertTrue((self.df[self.df['存储'].str.contains('LPDDR5T')].storage==1024).all())
    def test_price_only(self):
        r=rank(self.df,dict(price=1,weight=0,storage=0,refresh=0,screen=0),6.5)
        self.assertEqual(r.iloc[0].price,self.df.price.min())
        self.assertTrue(r.score.between(0,1).all())
    def test_target_only(self):
        r=rank(self.df,dict(price=0,weight=0,storage=0,refresh=0,screen=1),6.1)
        self.assertEqual(r.iloc[0].screen,6.1)
    def test_singleton(self):
        # New contract: one independent model is not a meaningful relative recommendation.
        with self.assertRaises(ValueError):
            rank(self.df.head(1),dict(price=1,weight=1,storage=1,refresh=1,screen=1),6.5)
    def test_zero_weights(self):
        with self.assertRaises(ValueError): rank(self.df,dict.fromkeys(['price','weight','storage','refresh','screen'],0),6.5)


class StableIdTests(unittest.TestCase):
    """稳定配置 ID 的性质。

    旧实现用 Excel 行号生成 ROW-xxx，取决于行顺序，新增或重排数据会让所有
    ID 漂移。工作文档第 3 章要求替换为维护在数据文件中的稳定 SKU ID，
    因此这里逐条钉住该行为，避免以后回退。
    """

    @classmethod
    def setUpClass(cls): cls.df=load_data()

    def test_no_legacy_row_ids(self):
        self.assertFalse(self.df['id'].str.startswith(ROW_ID_LEGACY_PREFIX).any())

    def test_ids_unique(self):
        self.assertTrue(self.df['id'].is_unique)

    def test_id_independent_of_row_order(self):
        shuffled=self.df.sample(frac=1,random_state=0).reset_index(drop=True)
        recomputed=[
            stable_id(row['产品名称'],row['运行内存'],row['存储'])
            for _,row in shuffled.iterrows()
        ]
        self.assertEqual(set(recomputed),set(self.df['id']))

    def test_same_model_different_storage_gets_different_ids(self):
        for name in ('iPhone 15','Redmi K80'):
            group=self.df[self.df['产品名称']==name]
            self.assertTrue(group['id'].is_unique,name)
            self.assertGreater(group['存储'].nunique(),1,name)

    def test_adding_record_keeps_existing_ids(self):
        extra=pd.DataFrame([{
            '产品名称':'测试新增机型','运行内存':'12GB','存储':'512GB','重量':'190g',
            '后摄像头':'50MP','前摄像头':'16MP','处理器':'X','屏幕大小':'6.5英寸',
            '分辨率':'2000*1000','屏幕刷新率':'120Hz','充电功率':'50W','价格':'2999元',
        }])
        import tempfile
        from pathlib import Path as P
        source=P(__file__).resolve().parents[1]/'data'/'phones.xlsx'
        with tempfile.TemporaryDirectory() as directory:
            target=P(directory)/'extended.xlsx'
            pd.concat([pd.read_excel(source),extra],ignore_index=True).to_excel(target,index=False)
            extended=load_data(target)
        self.assertEqual(len(extended),len(self.df)+1)
        self.assertTrue(set(self.df['id'])<=set(extended['id']))

    def test_duplicate_identity_rejected(self):
        source=Path(__file__).resolve().parents[1]/'data'/'phones.xlsx'
        raw=pd.read_excel(source)
        doubled=pd.concat([raw,raw.head(1)],ignore_index=True)
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'doubled.xlsx'
            doubled.to_excel(target,index=False)
            with self.assertRaises(ValueError):
                load_data(target)

    def test_ranking_unchanged_by_id_scheme(self):
        """排序主键是 score；ID 只作为同分时的稳定次序，不得改变得分。"""
        result=rank(self.df,dict(price=35,weight=20,storage=20,refresh=15,screen=10),6.5)
        self.assertAlmostEqual(result.iloc[0].score,0.684495,places=5)
        self.assertLessEqual(result.iloc[0].price,result.iloc[-1].price)
        self.assertTrue(result.score.is_monotonic_decreasing)

if __name__=='__main__': unittest.main()
