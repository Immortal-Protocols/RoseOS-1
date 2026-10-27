import unittest
from worker import Worker, NeedsAssistance, song_matches, checked_screen, verified_player, PACKAGE

def screen(rows, snapshot='fresh'):
    return {'status':'ok','packageName':PACKAGE,'snapshotId':snapshot,'truncated':False,'nodes':rows}
def node(i, text='', **kw):
    return {'id':i,'text':text,'visible':True,'enabled':True,**kw}
def search(query='', results=True):
    rows=[node(91,query,editable=True, focused=True, **{'class':'android.widget.AutoCompleteTextView'})]
    if results:
        rows += [node(23,'Hello',clickTarget=56),node(24,'Song • Adele',clickTarget=56),
                 node(43,'Hello',clickTarget=57),node(44,'Song • Lionel Richie',clickTarget=57)]
    return screen(rows)
def full(playing=True):
    return screen([node(5,'Hello'),node(6,'Adele'),node(7,description='Player Queue'),node(8,description='Pause' if playing else 'Play')])

class Tests(unittest.TestCase):
    def test_song_metadata_must_share_target(self):
        self.assertEqual(song_matches(search(),'Hello','Adele'),[{'target':56,'label':'Hello','artist':'Adele'}])
        self.assertEqual(song_matches(screen([node(1,'Hello',clickTarget=2),node(3,'Song • Adele',clickTarget=4)]),'Hello','Adele'),[])
    def test_first_ranked_song_can_be_live(self):
        s=screen([node(1,'Hello (Live)',clickTarget=2),node(3,'Song • Adele',clickTarget=2),node(4,'Hello',clickTarget=5),node(6,'Song • Adele',clickTarget=5)])
        self.assertEqual(song_matches(s,'Hello','Adele'),[{'target':2,'label':'Hello (Live)','artist':'Adele'}])
    def test_duplicate_listings_use_first_result(self):
        s=search();s['nodes'] += [node(80,'Hello',clickTarget=82),node(81,'Song • Adele',clickTarget=82)]
        self.assertEqual(song_matches(s,'Hello','Adele'),[{'target':56,'label':'Hello','artist':'Adele'}])
    def test_lyric_excerpt_is_not_the_song_title(self):
        s=screen([node(1,"And I just can't get enough",clickTarget=5,viewId='com.apple.android.music:id/eyebrow'),node(2,"Just Can't Get Enough",clickTarget=5,viewId='com.apple.android.music:id/title'),node(3,'Song • Depeche Mode',clickTarget=5,viewId='com.apple.android.music:id/subtitle')])
        self.assertEqual(song_matches(s,"Just Can't Get Enough",'Depeche Mode')[0]['label'],"Just Can't Get Enough")
    def test_album_results_are_skipped(self):
        s=screen([node(1,'Hello',clickTarget=2),node(3,'Album • Adele',clickTarget=2)])
        self.assertEqual(song_matches(s,'Hello','Adele'),[])
    def test_rejects_wrong_package_incomplete_and_protected(self):
        for override in [{'packageName':'another.app'}, {'truncated':True}, {'nodes':[node(1,password=True)]}]:
            with self.assertRaises(NeedsAssistance): checked_screen({**screen([]),**override})
    def test_hidden_lyrics_player_is_not_mistaken_for_search(self):
        from worker import player
        s=search();s['nodes'].append(node(88,viewId='com.apple.android.music:id/song_lyrics_word'))
        self.assertTrue(player(s))
    def test_verification_requires_artist_controls_and_playing_state(self):
        self.assertTrue(verified_player(full(),'Hello','Adele'))
        self.assertFalse(verified_player(full(False),'Hello','Adele'))
        self.assertFalse(verified_player(full(),'Hello','Other'))
    def test_reuses_fresh_targets_without_model_or_coordinate_taps(self):
        requests=[];state=search('',False)
        def phone(req):
            nonlocal state
            requests.append(req)
            if req['action']=='open_app':return {'status':'opened','packageName':PACKAGE,'observation':state}
            if req['action']=='set_text':
                self.assertEqual(req['nodeId'],91);state=search('Hello Adele');return {'status':'action_accepted'}
            if req['action']=='inspect_ui':return state
            if req['action']=='back':
                state=screen([node(9,description='playing Hello',clickTarget=9),node(10,description='Pause')]);return {'status':'action_accepted'}
            if req['action']=='tap_label':
                if req['label']=='Hello':self.assertEqual(req['nodeId'],56);state=full()
                elif req['label']=='Pause':state=screen([node(9,description='playing Hello',clickTarget=9),node(10,description='Play')])
                else:self.fail(req)
                return {'status':'action_accepted','observation':state}
            self.fail(req)
        r=Worker(phone).run('Hello','Adele',True)
        self.assertEqual(r['status'],'verified_playing_then_paused')
        self.assertEqual(r['modelCalls'],0)
        self.assertEqual([r['action'] for r in requests],['open_app','inspect_ui','set_text','inspect_ui','tap_label','back','inspect_ui','tap_label'])
    def test_never_repeats_a_play_tap_after_unknown_outcome(self):
        requests=[];t=[0]
        def phone(req):
            requests.append(req)
            if req['action']=='open_app':return {'status':'opened','packageName':PACKAGE,'observation':search()}
            if req['action']=='set_text':return {'status':'action_accepted'}
            if req['action']=='inspect_ui':return search('Hello Adele')
            if req['action']=='tap_label':return {'status':'action_accepted','observation':search('Hello Adele')}
        result=Worker(phone,now=lambda:t[0],sleep=lambda _:t.__setitem__(0,t[0]+1)).run('Hello','Adele')
        self.assertEqual(result['status'],'needs_assistance')
        self.assertEqual(sum(r['action']=='tap_label' for r in requests),1)

if __name__=='__main__':unittest.main()
