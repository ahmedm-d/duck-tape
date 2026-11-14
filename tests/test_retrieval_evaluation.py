from scripts import evaluate_retrieval


def test_retrieval_metrics_penalize_missing_files_and_irrelevant_ranges():
    measured = evaluate_retrieval.score_evidence([{'path': 'api.py', 'start_line': 1, 'end_line': 3}, {'path': 'extra.py', 'start_line': 1, 'end_line': 1}],
                                               [('api.py', 2, 3), ('helper.py', 1, 1)])
    assert measured == {'file_recall': .5, 'file_precision': .5, 'range_recall': .5, 'range_precision': .5}
