from backend import retrieval, repository, providers


def test_live_qa_rejects_fabricated_citations(tmp_path,monkeypatch):
    (tmp_path/'value.py').write_text('VALUE = 7\n')
    index=repository.index_repository(tmp_path)
    monkeypatch.setattr(providers,'health',lambda:{'available':True})
    monkeypatch.setattr(providers,'complete',lambda *args:({'answer':'Claim','citations':[{'path':'absent.py','start_line':1,'end_line':1}]},{'model':'transport fixture'}))
    answer=retrieval.answer(index,'VALUE',live=True)
    assert answer['mode']=='offline structural'
    assert 'absent.py' not in str(answer['citations'])
    monkeypatch.setattr(providers,'complete',lambda *args:({'answer':'VALUE is seven.','citations':[{'path':'value.py','start_line':1,'end_line':1}]},{'model':'transport fixture'}))
    answer=retrieval.answer(index,'VALUE',live=True)
    assert answer['mode']=='live evidence-linked answer'
    assert answer['citations'][0]['content']=='VALUE = 7'
