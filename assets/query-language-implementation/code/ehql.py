# These are inline dependencies! See: https://packaging.python.org/en/latest/specifications/inline-script-metadata/#script-type
# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "lark>=1.3.1,<2.0",
#   "pyspark-connect>=4.1.2,<5.0",
# ]
# ///

"""
A self-contained skeleton implementation of Entity History Query Language (EHQL).

Companion to: https://nchammas.com/writing/custom-query-language-implementation

Usage:
    uv run ehql.py
"""

import functools
import operator
import textwrap
import pyspark.sql.functions as sqlf
from lark import Lark, Transformer, v_args
from lark.indenter import Indenter
from pyspark.sql import SparkSession


EHQL_GRAMMAR = r"""
%import common.ESCAPED_STRING -> QUOTED_STRING
%import common.WS_INLINE
%import common.SQL_COMMENT

%ignore WS_INLINE
%ignore SQL_COMMENT

%declare _INDENT _DEDENT

?start: _NEWLINE* history_clause

history_clause: "history" "contains" ":" _history_body
_history_body: _NEWLINE _INDENT [history_pattern _NEWLINE]+ _DEDENT
history_pattern: event_name
event_name: QUOTED_STRING

_NEWLINE: (/\r?\n[\t ]*/ | SQL_COMMENT)+
"""


class EHQLIndenter(Indenter):
    NL_type = '_NEWLINE'
    OPEN_PAREN_types = []
    CLOSE_PAREN_types = []
    INDENT_type = '_INDENT'
    DEDENT_type = '_DEDENT'
    tab_len = 2


class EvaluateEHQLSkeleton(Transformer):
    def __init__(self, spark):
        self.spark = spark

    def history_clause(self, history_patterns):
        return (
            self.spark
            .table("maintenance_history")
            # This adds the columns like `__has_oil_change` which we
            # defined in `history_pattern()`.
            .withColumns({
                condition_name: column
                for (condition_name, column) in history_patterns
            })
            .groupBy("vehicle_id")
            # This aggregation with `bool_or` helps us identify if a vehicle
            # had a particular event anywhere across its maintenance history.
            .agg(*[
                sqlf.bool_or(sqlf.col(condition_name)).alias(condition_name)
                for (condition_name, _) in history_patterns
            ])
            .where(
                # PySpark uses the `&` bitwise operator to `AND` conditions
                # together. So this breaks down to something like:
                #     col("condition1") & col("condition2") & ...
                functools.reduce(
                    operator.and_,
                    [
                        sqlf.col(condition_name)
                        for (condition_name, _) in history_patterns
                    ]
                )
            )
        )

    @v_args(inline=True)
    def history_pattern(self, event_name: str):
        event_name_slug = event_name.lower().replace(" ", "_")
        condition_name = f"__has_{event_name_slug}"
        return (
            condition_name,
            sqlf.col("work_done") == event_name,
        )

    @v_args(inline=True)
    def event_name(self, quoted_string: str):
        return quoted_string.strip('"')


MAINTENANCE_HISTORY = [
    (224,  "2023-10-13 14:33:17", "oil change",                "0W-20"),
    (224,  "2023-10-13 14:50:09", "oil filter change",         None),
    (7889, "2010-01-03 09:11:42", "timing belt replacement",   None),
    (8031, "2015-08-30 12:03:31", "diagnostic",                "P1155"),
    (8031, "2016-02-15 10:22:44", "diagnostic",                "P1155"),
    (8031, "2016-07-01 15:45:22", "diagnostic",                "P1155"),
    (8031, "2016-07-07 11:01:02", "o2 sensor replaced",        None),
    (1122, "2022-03-15 09:30:00", "intake upgrade",            "performance"),
    (1122, "2022-03-20 11:20:00", "exhaust upgrade",           "sport"),
    (1122, "2022-03-25 14:15:00", "fuel/air ratio reconfig",   "custom"),
    (3344, "2023-01-10 08:12:13", "oil change",                "5W-20"),
    (3344, "2023-01-10 08:45:00", "transmission fluid change", "AW-1"),
    (3344, "2023-01-25 13:20:00", "transmission rebuild",      None),
    (5566, "2023-05-12 11:30:00", "diagnostic",                "P1155"),
    (5566, "2023-06-15 14:20:00", "diagnostic",                "P1155"),
    (5566, "2023-07-20 16:45:00", "diagnostic",                "P1155"),
    (9900, "2023-08-01 10:00:00", "intake upgrade",            "performance"),
    (9900, "2023-09-15 11:30:00", "exhaust upgrade",           "sport"),
]


def main():
    spark = SparkSession.builder.remote("local[*]").getOrCreate()

    # Load the sample data into a temporary view so our transformer can query it.
    (
        spark
        .createDataFrame(
            MAINTENANCE_HISTORY,
            schema=["vehicle_id", "time", "work_done", "detail"],
        )
        .createOrReplaceTempView("maintenance_history")
    )

    query = textwrap.dedent(
        """\
        -- This is an example EHQL query.
        history contains:
            "oil change"
            "transmission fluid change"
        """
    )

    parser = Lark(EHQL_GRAMMAR, parser='lalr', postlex=EHQLIndenter())
    parse_tree = parser.parse(query)

    print("Query")
    print("-----")
    print(query)

    print("Parse Tree")
    print("----------")
    print(parse_tree.pretty())

    # Transform the parse tree into a Spark DataFrame and run it.
    result = EvaluateEHQLSkeleton(spark).transform(parse_tree)

    print("Query Result")
    print("------------")
    # Up until we complete the definition of `history_clause` in the transformer,
    # the result is still a tree that we can pretty print.
    # print(result.pretty())
    result.select("vehicle_id").show(truncate=False)
    # result.show(truncate=False)


if __name__ == "__main__":
    main()
