"""Alignment must stay on the source clock, independently of rendered lyrics."""
from test_preparation import prepared,promote
from _gbr_preparation.service import canonical,digest


def alignment_result(prep,gid,starts,kind='lyric_alignment',projected=False,language='en'):
    source=prep.source(gid)[1]
    recipe=prep.lyric_recipe(gid,{'version':1,'language':language})
    identifier=prep._insert(gid,kind,recipe,digest(source),'done')
    words=recipe['words']
    for word,start in zip(words,starts):
        word.update(start=start,end=start+.2,confidence=.9,estimated=False)
        if projected:word['id']='section_'+word['id']
    payload={'source_sha256':digest(source),'language':language,'words':words,'approved':kind=='lyric_edit'}
    path=prep.bundle(gid)/'_derived'/(identifier+'.json');path.parent.mkdir(exist_ok=True)
    path.write_text(canonical(payload),encoding='utf-8')
    prep.db.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
        (identifier+'-output',kind,digest(path),str(path),path.stat().st_size,
         canonical({'processing_task_id':identifier,'source_generation_id':gid})))
    return identifier


def test_loading_source_alignment_does_not_load_a_rendered_lyric_revision(prepared):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    aligned=alignment_result(prep,gid,[.2,1,2,3])
    rendered=alignment_result(prep,gid,[.4,1.2,2.2,3.2],'lyric_edit',projected=True)
    # A transparent render can share the source audio hash. Its timeline IDs
    # and offsets must never be imported as source-word alignment.
    assert prep.editorial(gid)['lyric_task_id']==rendered
    result=prep.editorial(gid,alignment_only=True)
    assert result['lyric_task_id']==aligned
    assert [word['id'] for word in result['words']]==['w00000','w00001','w00002','w00003']
    assert result['words'][0]['start']==.2
    assert not result['approved']


def test_alignment_lookup_never_returns_manual_edits_as_a_completed_alignment(prepared):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    alignment_result(prep,gid,[.4,1.2,2.2,3.2],'lyric_edit')
    result=prep.editorial(gid,alignment_only=True)
    assert result['lyric_task_id'] is None
    assert result['timed_words']==0


def test_alignment_lookup_uses_the_requested_language(prepared):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    english=alignment_result(prep,gid,[.2,1,2,3])
    german=alignment_result(prep,gid,[.4,1.2,2.2,3.2],language='de')
    assert prep.editorial(gid,alignment_only=True,alignment_language='en')['lyric_task_id']==english
    assert prep.editorial(gid,alignment_only=True,alignment_language='de')['lyric_task_id']==german
    assert prep.editorial(gid,alignment_only=True,alignment_language='fr')['lyric_task_id'] is None
