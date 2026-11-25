import datetime as dt

import pytest

from rssac002_dashboard.parsing import ReportError, parse_report

from .conftest import report_text

DAY = dt.date(2026, 5, 21)


def test_traffic_volume_is_parsed():
    report = parse_report(report_text("traffic-volume", DAY))
    assert report.metric == "traffic-volume"
    assert report.day == DAY
    assert report.data["dns-udp-queries-received-ipv4"] == 700
    assert report.warnings == []


def test_omitted_fields_count_as_zero():
    header = "version: rssac002v5\nservice: nl\nstart-period: 2026-05-21T00:00:00Z\n"
    text = header + "metric: unique-sources\nnum-sources-ipv4: 5\n"
    assert parse_report(text).data == {"num-sources-ipv4": 5, "num-sources-ipv6": 0, "num-sources-ipv6-aggregate": 0}


def test_null_unique_sources_value_is_not_reported_rather_than_zero():
    report = parse_report(report_text("unique-sources", DAY, **{"num-sources-ipv6-aggregate": None}))
    assert report.data == {"num-sources-ipv4": 80, "num-sources-ipv6": 20, "num-sources-ipv6-aggregate": None}
    assert report.warnings == ["num-sources-ipv6-aggregate has no value and is shown as not reported"]


def test_null_is_still_rejected_where_it_would_hide_traffic():
    with pytest.raises(ReportError, match="must be an integer"):
        parse_report(report_text("traffic-volume", DAY, **{"dns-udp-queries-received-ipv4": None}))


def test_rcode_minus_one_is_kept_and_zero_counts_dropped():
    report = parse_report(report_text("rcode-volume", DAY, **{"4": 0}))
    assert report.data["rcodes"] == {"-1": 15, "0": 800, "2": 20, "3": 150, "5": 15}


def test_histograms_are_sorted_by_size():
    report = parse_report(report_text("traffic-sizes", DAY))
    assert report.data["udp-response-sizes"] == [[96, 500], [112, 300], [1232, 85]]
    assert report.data["tcp-response-sizes"] == [[1024, 90], [4096, 10]]


@pytest.mark.parametrize(
    "start",
    [
        "2026-05-21T00:00:00Z",
        "'2026-05-21T00:00:00+00:00'",
        "2026-05-21T02:00:00+02:00",
        "2026-05-21 00:00:00",
        "2026-05-21",
    ],
)
def test_start_period_formats(start):
    text = f"version: rssac002v5\nservice: nl\nstart-period: {start}\nmetric: unique-sources\n"
    assert parse_report(text).start == dt.datetime(2026, 5, 21, tzinfo=dt.UTC)


def test_window_not_starting_at_midnight_utc_is_a_warning():
    text = "version: rssac002v5\nservice: nl\nstart-period: 2026-05-20T23:42:00Z\nmetric: unique-sources\n"
    report = parse_report(text)
    assert report.day == dt.date(2026, 5, 20)
    assert "23:42" in report.warnings[0]


def test_unknown_field_is_a_warning_not_an_error():
    report = parse_report(report_text("traffic-volume", DAY, **{"something-else": 1}))
    assert report.warnings == ["ignored unknown field 'something-else'"]


@pytest.mark.parametrize(
    ("metric", "overrides", "message"),
    [
        ("traffic-volume", {"dns-udp-queries-received-ipv4": -1}, "must not be negative"),
        ("traffic-volume", {"dns-udp-queries-received-ipv4": 1.5}, "must be an integer"),
        ("traffic-volume", {"dns-udp-queries-received-ipv4": True}, "must be an integer"),
        ("traffic-volume", {"version": "v5"}, "version"),
        ("traffic-volume", {"service": ""}, "service is missing"),
        ("rcode-volume", {"65536": 1}, "rcode must be between"),
        ("traffic-sizes", {"udp-request-sizes": {"30-45": 1}}, "16-byte boundary"),
        ("traffic-sizes", {"udp-request-sizes": {"32-63": 1}}, "span exactly 16"),
        ("traffic-sizes", {"udp-request-sizes": {"32-": 1}}, "may be open-ended"),
        ("traffic-sizes", {"udp-request-sizes": {"32-47": 1, "32-47 ": 1}}, "is not a size bucket"),
        ("traffic-sizes", {"udp-request-sizes": {"288-300": 1}}, "span exactly 16"),
        ("traffic-sizes", {"udp-request-sizes": [1, 2]}, "must be a mapping"),
    ],
)
def test_wrong_values_are_rejected(metric, overrides, message):
    with pytest.raises(ReportError, match=message):
        parse_report(report_text(metric, DAY, **overrides))


def test_steps_beyond_the_defined_maximum_are_counted_in_the_open_ended_step():
    """Some root servers report 16-byte steps past 288 bytes for queries; the totals must survive."""
    sizes = {"udp-request-sizes": {"32-47": 5, "288-303": 2, "1008-1023": 1, "288-": 4}}
    report = parse_report(report_text("traffic-sizes", DAY, **sizes))
    assert report.data["udp-request-sizes"] == [[32, 5], [288, 7]]
    assert report.warnings == ["udp-request-sizes has steps beyond 288 bytes; they are counted in the open-ended step"]
    assert parse_report(report_text("traffic-sizes", DAY)).warnings == []


def test_sixteen_bit_rcodes_are_accepted():
    assert parse_report(report_text("rcode-volume", DAY, **{"65535": 9})).data["rcodes"]["65535"] == 9


def test_metric_must_match_the_file_name():
    with pytest.raises(ReportError, match="named as rcode-volume but contains traffic-volume"):
        parse_report(report_text("traffic-volume", DAY), expected_metric="rcode-volume")


@pytest.mark.parametrize("text", ["", "- a\n- b\n", "a: [unclosed", "---\na: 1\n---\nb: 2\n"])
def test_things_that_are_not_one_report(text):
    with pytest.raises(ReportError):
        parse_report(text)


def test_tls_and_quic_fields_of_an_operator_are_read_under_one_name():
    """B and H root report these transports in prefixed fields; H called them dot and doq at first."""
    b = parse_report(report_text("traffic-volume", DAY, **{"b-dns-tls-queries-received-ipv4": 30}))
    assert b.data["dns-tls-queries-received-ipv4"] == 30 and b.warnings == []
    assert "dns-tls-queries-received-ipv6" not in b.data  # not reported is not the same as zero
    early = {"h-dns-dot-queries-received-ipv6": 7, "h-dns-doq-responses-sent-ipv4": 2}
    late = {"h-dns-tls-queries-received-ipv6": 7, "h-dns-quic-responses-sent-ipv4": 2}
    assert parse_report(report_text("traffic-volume", DAY, **early)).data == (
        parse_report(report_text("traffic-volume", DAY, **late)).data
    )
    assert parse_report(report_text("traffic-volume", DAY, **late)).data["dns-quic-responses-sent-ipv4"] == 2
    assert "dns-tls-queries-received-ipv4" not in parse_report(report_text("traffic-volume", DAY)).data


def test_tls_responses_named_queries_sent_are_read_as_responses_with_a_note():
    report = parse_report(report_text("traffic-volume", DAY, **{"b-dns-tls-queries-sent-ipv4": 29}))
    assert report.data["dns-tls-responses-sent-ipv4"] == 29
    assert report.warnings == ["b-dns-tls-queries-sent-ipv4 is read as responses sent"]


def test_tls_and_quic_size_histograms_are_read():
    sizes = {"h-tls-request-sizes": {"32-47": 4, "288-": 1}, "h-doq-response-sizes": {"848-863": 2}}
    report = parse_report(report_text("traffic-sizes", DAY, **sizes))
    assert report.data["tls-request-sizes"] == [[32, 4], [288, 1]]
    assert report.data["quic-response-sizes"] == [[848, 2]]
    assert "tls-response-sizes" not in report.data and report.warnings == []


@pytest.mark.parametrize(
    ("metric", "field"),
    [
        ("traffic-volume", "b-dns-sctp-queries-received-ipv4"),  # a transport the dashboard does not know
        ("traffic-volume", "dns-tls-queries-received-ipv4"),  # no prefix: not how operators name their own fields
        ("traffic-volume", "b-dns-tls-queries-received-ipv5"),
        ("traffic-sizes", "h-sctp-request-sizes"),
    ],
)
def test_other_unknown_fields_are_still_ignored_with_a_warning(metric, field):
    report = parse_report(report_text(metric, DAY, **{field: 1 if metric == "traffic-volume" else {"32-47": 1}}))
    assert report.warnings == [f"ignored unknown field {field!r}"]
    assert set(report.data) == set(parse_report(report_text(metric, DAY)).data)


@pytest.mark.parametrize(
    ("metric", "overrides", "message"),
    [
        ("traffic-volume", {"h-dns-tls-queries-received-ipv4": -1}, "must not be negative"),
        ("traffic-volume", {"h-dns-tls-queries-received-ipv4": 1, "h-dns-dot-queries-received-ipv4": 1}, "both give"),
        ("traffic-volume", {"b-dns-tls-responses-sent-ipv4": 1, "b-dns-tls-queries-sent-ipv4": 1}, "both give"),
        ("traffic-sizes", {"h-tls-request-sizes": {"30-45": 1}}, "16-byte boundary"),
        ("traffic-sizes", {"h-tls-request-sizes": {"32-47": 1}, "h-dot-request-sizes": {"32-47": 1}}, "both give"),
    ],
)
def test_wrong_tls_and_quic_values_are_rejected(metric, overrides, message):
    with pytest.raises(ReportError, match=message):
        parse_report(report_text(metric, DAY, **overrides))
