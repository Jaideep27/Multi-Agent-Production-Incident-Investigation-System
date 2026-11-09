"""Small helpers for turning database rows into plain JSON-ready values."""


def epoch_ms(value):
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def rows_as_dicts(cursor):
    names = []
    for column in cursor.description:
        names.append(column.name)
    rows = []
    for values in cursor.fetchall():
        row = {}
        for index in range(len(names)):
            row[names[index]] = values[index]
        rows.append(row)
    return rows


def top_hypothesis(report):
    if not report:
        return None
    hypotheses = report.get("hypotheses") or []
    if not hypotheses:
        return None
    return hypotheses[0]
