import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'phone'))
from observations import compact_command,compact_result
class ObservationTests(unittest.TestCase):
 def test_ids_labels_and_result_preserved(self):
  nodes=[{'id':0,'text':'','description':'','class':'Layout'}, {'id':4,'text':'12','description':'Result 12'}, {'id':16,'text':'6','description':'6','clickable':True}, {'id':20,'text':'3','description':'3','clickable':True}]
  r=compact_result({'status':'ok','snapshotId':'abc','packageName':'calculator','truncated':False,'nodes':nodes})
  self.assertEqual(r['nodes'],[{'id':4,'text':'12','description':'Result 12'}, {'id':16,'text':'6','actions':['tap']}, {'id':20,'text':'3','actions':['tap']}])
  self.assertEqual(r['snapshotId'],'abc')
 def test_disabled_secret_and_unlabelled_controls(self):
  r=compact_result({'nodes':[{'id':8,'editable':True,'password':True,'text':'[password]','enabled':False},{'id':9,'scrollable':True,'bounds':[0,1,2,3]}]})
  self.assertTrue(r['nodes'][0]['password']);self.assertFalse(r['nodes'][0]['enabled']);self.assertEqual(r['nodes'][1]['bounds'],[0,1,2,3])
 def test_repeated_label_bounds_preserved(self):
  r=compact_result({'nodes':[{'id':29,'text':'Submit','clickable':True,'bounds':[240,1000,460,1500]}]})
  self.assertEqual(r['nodes'][0]['bounds'],[240,1000,460,1500])
 def test_action_and_observation_both_retained(self):
  r=compact_command({'id':'request','action':'tap_label','created':123,'status':'completed','result':{'status':'action_accepted','observation':{'status':'ok','snapshotId':'fresh','nodes':[{'id':2,'text':'3'}]}}})
  self.assertEqual(r['result']['status'],'action_accepted');self.assertEqual(r['result']['observation']['snapshotId'],'fresh');self.assertNotIn('created',r)
unittest.main()
