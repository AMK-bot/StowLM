"""D224 gates: current local masks and the immutable original Spark reference."""
from experiments.mvp.reader.gates_v2 import gate_report_v2


def gate_report(tuned, untrained, spark):
    if tuned['count'] < 120 or tuned['loosening_count'] < 40:
        raise ValueError('sealed v3 requires at least 120 rows and 40 loosening requests')
    for report in (tuned, untrained, spark):
        if report.get('missing_count', 0) != 0:
            raise ValueError('all registered arms must have complete predictions')
    for report in (tuned, untrained, spark):
        if not report.get('schema_constrained') or report.get('answer_language') != 'reader-schema-v3':
            raise ValueError('arms must identify the registered schema answer contract')
    if (not isinstance(tuned.get('decoder_sha256'), str) or not tuned['decoder_sha256']
            or tuned.get('decoder_sha256') != untrained.get('decoder_sha256')):
        raise ValueError('local arms must use the identical decoder')
    if (not isinstance(tuned.get('domain_words_sha256'), str) or not tuned['domain_words_sha256']
            or tuned.get('domain_words_sha256') != untrained.get('domain_words_sha256')):
        raise ValueError('local arms must use identical context class vocabulary')
    report = gate_report_v2(tuned, untrained, spark)
    report['criteria'].update(min_count=120, min_loosening_count=40)
    report['spark_comparison'] = 'Fixed original sealed-v3 Spark reference; not rerun under the updated decoder.'
    report['all_pass'] = all(report[key] for key in ('R1', 'R2', 'R3'))
    return report
