import sys,tempfile,unittest,json
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,'/home/djpi/sunday')
from negative_dataset import Dataset
from aiohttp.test_utils import TestClient,TestServer
from aliases import create_app
class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_private_read_review_and_delete(self):
  with tempfile.TemporaryDirectory() as tmp:
   ds=Dataset(Path(tmp)/'dataset');ident='e'*32;ds.save(b'\x00\x00'*1600,{'event_id':ident,'mode':'enforce','accepted':False,'t':'2026-10-27T05:00:00+0530','text':'sample'},{})
   with patch('negative_dataset.Dataset',return_value=ds):app=create_app(token='test')
   async with TestClient(TestServer(app)) as client:
    path='/v1/wake-negatives';headers={'Authorization':'Bearer test'}
    self.assertEqual((await client.post(path,json={'action':'list'})).status,401)
    r=await client.post(path,headers=headers,json={'action':'list'});self.assertEqual((await r.json())['total'],1)
    r=await client.post(path,headers=headers,json={'action':'audio','id':'../secrets'});self.assertEqual(r.status,400)
    r=await client.post(path,headers=headers,json={'action':'audio','id':ident});self.assertIn('audio_base64',await r.json())
    r=await client.post(path,headers=headers,json={'action':'review','id':ident,'review':'background'});self.assertEqual((await r.json())['review'],'background')
    r=await client.post(path,headers=headers,json={'action':'delete','id':ident});self.assertTrue((await r.json())['deleted']);self.assertEqual(ds.listing()['total'],0)
if __name__=='__main__':unittest.main()
