---
layout: post
title: "Implementing a Custom Query Language with Python and Apache Spark"
permalink: /writing/:title
tags: [dsl, query-language]
---

In [my previous post][prev] I walked through how I designed a new query language called Entity History Query Language (EHQL) for a group of semi- and non-technical analysts working with vehicle maintenance data. Here I will show you how to implement a working skeleton of that language using Python and Apache Spark.

[prev]: {% post_url 2026-05-11-custom-query-language-design %}

There are many approaches you could take to implement a query language. I chose Python because I enjoy working with it, and because it's a very popular language in the data processing space. I also knew upfront that I wanted my custom language to somehow run on top of Apache Spark because I work with it every day and the data I was interested in querying was already stored as Parquet and registered in a central catalog. That context alone greatly reduces the space of possible implementation approaches, and that's a great thing! I didn't need (or want) to design a custom storage format or query engine, both of which are major, major undertakings. I just needed the ability to compile my custom language down into queries that Spark could then execute for me.

If you're implementing your own language, there are broadly three steps you'll need to take:

1. Define a grammar, usually in some form of [EBNF].
2. Use that grammar to construct a parse tree from the source text.
3. Transform that parse tree into whatever final form your program should take.

<!-- In my case, that final form is going to be a query that Spark can execute. -->

[EBNF]: https://en.wikipedia.org/wiki/Extended_Backus–Naur_form

EHQL has a number of interesting [features], but it would be too much to walk through implementing them all in this post. Instead, I'm going to focus on the following example query and use it to implement a skeleton of the language that will nonetheless demonstrate the key technologies and techniques involved.

[features]: {% post_url 2026-05-11-custom-query-language-design %}#designing-the-syntax

```sql
-- This is an example EHQL query.
history contains:
  "oil change"
  "transmission fluid change"
```

This query finds all vehicles in our database that have had both an oil change and transmission fluid change at any time in their maintenance history.

Here is a [self-contained solution][script] you can run locally to follow along as I build out the implementation.

## Defining the Grammar

If you're working with Python, the right library to use to define your grammar is [Lark].[^standard] I believe in the JVM ecosystem most people use [ANTLR]. ANTLR does have a Python implementation, but a quick survey of both parsing libraries tells me that Lark is [faster, more popular, and more feature rich][lark-feature]. And while I personally haven't worked with ANTLR to compare, I can say that I enjoyed working with Lark and didn't feel its design was obtuse or constraining in any way.

[^standard]: [Lark is the standard parsing toolkit.][standard]
[Lark]: https://github.com/lark-parser/lark
[standard]: https://www.gnu.org/fun/jokes/ed-msg.txt
[ANTLR]: https://www.antlr.org
[lark-feature]: https://github.com/lark-parser/lark/tree/eb015d1b5c236b22bf3215fd4a76c891262e1bf8#comparison-to-other-libraries

Here is the Lark grammar for the minimal skeleton of EHQL that we are walking through in this post:

```lark
# ehql.lark
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
```

Lark grammars are expressed in [EBNF], with some added features specific to Lark. The [`%import` statements][lark-imp] at the top, for example, are importing predefined rules that Lark offers so we can use them in our own grammar. When you import a rule, you can also choose to rename it, which is what `ESCAPED_STRING -> QUOTED_STRING` is doing. `start` is the default rule Lark will look for as the root of the grammar, though you can rename it. [The leading `?`][lark-q] in `?start` tells Lark to exclude it from the parse tree if it has a single child, which it does.

[lark-imp]: https://lark-parser.readthedocs.io/en/stable/grammar.html#import
[lark-q]: https://lark-parser.readthedocs.io/en/stable/tree_construction.html#conditionally-inlining-rules-with

By convention, rules in block capitals are typically _terminals_, which basically means rules that don't in turn break down into more rules. In other words, terminals match some text and stop there.

- `QUOTED_STRING` matches strings with double quotes like `"hello"`.
- `WS_INLINE` matches inline whitespace like spaces and tabs, but not newlines.
- `SQL_COMMENT` matches SQL-style comments like `-- this is a comment`.

The two `%ignore` statements tell Lark to ignore text that matches those terminals. This is basically how we implement support for comments and make our language robust to trivial whitespace differences. For example, `history contains:` and <code>history&nbsp;&nbsp;&nbsp;&nbsp;contains:</code> will both parse the same thanks to `%ignore WS_INLINE`.

### Handling Significant Indentation

EHQL is like Python in that [it uses indentation][ind] to logically group statements together. The way this is implemented in the grammar is via these statements:

[ind]: {% post_url 2026-05-11-custom-query-language-design %}#significant-indentation

```lark
%declare _INDENT _DEDENT

history_clause: "history" "contains" ":" _history_body
_history_body: _NEWLINE _INDENT [history_pattern _NEWLINE]+ _DEDENT

_NEWLINE: (/\r?\n[\t ]*/ | SQL_COMMENT)+
```

The key structure is captured by `_history_body`, which contains one or more history patterns, each separated by a newline. The patterns are all indented one level relative to the parent `history_clause`. The `_NEWLINE` terminal is a bit interesting because it matches whitespace after the newline as well as comments; this is crucial for Lark to handle indentation and inline comments [correctly][tree]. Also interesting are the `_INDENT` and `_DEDENT` terminals. They don't have definitions in our grammar but they are declared and then used as part of `_history_body`. This is another consequence of how Lark handles languages with significant indentation, specifically.

[tree]: https://lark-parser.readthedocs.io/en/stable/examples/indented_tree.html

Why all the strangeness with how `_NEWLINE`, `_INDENT`, and `_DEDENT` are defined? The gist of it is this: Parsing text into a structured syntax tree generally happens in two stages, _lexing_ and _parsing_. Lexing a text means scanning the raw characters and converting them into _tokens_, which would be matched by the terminals of our grammar (i.e. the rules in block capitals). Parsing takes the tokens we generated and composes them into a structured tree based on the rules of our grammar.

When the lexing stage encounters a newline, the number of `_INDENT` or `_DEDENT` tokens it needs to generate will depend on what happened _before_ and what happens _after_ the newline. We won't be implementing support for [`any` and `all`][aa] in this post, but consider this example:

[aa]: {% post_url 2026-05-11-custom-query-language-design %}#against-boolean-operators

```sql
-- After each newline, does the lexer need to indent or dedent?
history contains: -- indent
  any of: -- indent
    "oil change" -- no indent or dedent
    "transmission fluid change" -- dedent 2x
```

In other words, the lexer needs to maintain some state about the indentation level as it goes through the text if it's going to generate indent and dedent tokens correctly. But lexers are typically stateless, so they cannot track this kind of information. I believe they are designed this way mainly so they can be simple and fast. So the way Lark addresses this problem is with a _postlex processor_ that runs after lexing but before parsing. The postlexer _does_ track some state and generates the indent and dedent tokens we need.

Lark offers a dedicated postlexer for significant indentation. All we need to do is subclass it and tell it which of our grammar's terminals correspond to the key newline, indent, and dedent tokens.

```python
from lark.indenter import Indenter


class EHQLIndenter(Indenter):
    NL_type = '_NEWLINE'
    OPEN_PAREN_types = []
    CLOSE_PAREN_types = []
    INDENT_type = '_INDENT'
    DEDENT_type = '_DEDENT'
    tab_len = 2
```

If EHQL supported breaking up long expressions across multiple lines using parentheses, we'd also have to specify the names of those tokens. That's because the indentation level would have to be frozen while inside the parentheses, regardless of what leading whitespace there was on each line. Since EHQL doesn't support this, we can leave the `*_PAREN_types` parts of `EHQLIndenter` empty.

<!--
For example, in Python:

```python
# After each newline, does the lexer need to indent or dedent?
if True:  # indent
    if (  # no indent or dedent!
        True and  # no indent or dedent!
                True  # no indent or dedent!
    ):  # indent
        print("Hello!")  # dedent 2x
```
-->

Finally, the `tab_len` parameter is there to translate the tab character `\t` into the appropriate number of spaces when determining indentation level. Note it is _not_ there to tell Lark how many spaces translate into a level of indentation, which is something I was initially confused by. The indentation level of a given line is determined automatically by the postlexer based on the number of leading spaces that line has relative to the previous line. There is no global rule that translates a fixed number of spaces into a specific indentation level. This means that separate blocks of code can be written with different numbers of leading spaces but parse to the same indentation level, [just like in Python][py-ind]. In practice, EHQL's convention is to use two spaces per indentation level, as compared to [Python's four][py4].

[py-ind]: https://docs.python.org/3/reference/lexical_analysis.html#indentation
[py4]: https://peps.python.org/pep-0008/#indentation

<!--
So if we look at the [Parsing Indentation example][tree] from the Lark documentation, the following texts will parse to the exact same parse tree given the same definition for `TreeIndenter`:

```python
# Using `·` instead of an actual space so they're easier to count.

a
# Indent level 1: 4 spaces
····b
c
# Indent level 1: 1 space
·d
# Indent level 2: 2 spaces
··e
# Indent level 3: 4 spaces
····f

a
# Indent level 1: 2 spaces
··b
c
# Indent level 1: 2 spaces
··d
# Indent level 2: 4 spaces
····e
# Indent level 3: 6 spaces
······f
```
-->

## Constructing the Parse Tree

Alright! We have our grammar, and we have the appropriate instructions for the postlexer that's going to handle EHQL's indentation. Now we can put them together and parse our example EHQL query into a parse tree.

```python
from lark import Lark
from pathlib import Path
from textwrap import dedent

grammar = Path("ehql.lark").read_text()
parser = Lark(grammar, parser='lalr', postlex=EHQLIndenter())
query = dedent(
    """
    -- This is an example EHQL query.
    history contains:
      "oil change"
      "transmission fluid change"
    """
)
parse_tree = parser.parse(query)
print(parse_tree.pretty())
```

First, a brief note about `parser='lalr'`. Lark supports a few [parsing algorithms][pa] that you can choose from when parsing your grammar. The main two are Earley and LALR(1). Earley is slower but much more flexible in what grammars it can handle. However, LALR(1) is powerful enough to parse "real" languages like Python and Java, so unless you're doing something exotic I would start with LALR(1) and only switch to something else if necessary. Notice also that, since our language has significant indentation, we had to pass our indenter specification to the `postlex` parameter.

[pa]: https://lark-parser.readthedocs.io/en/stable/parsers.html

Running the above will parse our sample query and print the following parse tree:

```
history_clause
  history_pattern
    event_name  "oil change"
  history_pattern
    event_name  "transmission fluid change"
```

Now that's pretty neat! We can see nodes in this tree that correspond to our grammar rules. Comments and inline whitespace have been correctly ignored thanks to the `%ignore` directive. Newlines and indentation tokens have been correctly converted into the appropriate structure and then dropped from the parse tree. This is important because we don't want or need to see tokens like `_NEWLINE`, `_INDENT`, or `_DEDENT` in the tree itself; we just need `event_name` to be a child of `history_pattern`, and `history_pattern` a child of `history_clause`, etc. This is a key detail of our Lark grammar: Rules that start with an underscore are parsed into the appropriate structure but then discarded so they don't litter the final parse tree.

## Converting the Parse Tree into a Query

At this point, we're ready to convert this parse tree into a query that Apache Spark can execute for us. Spark has a broad set of capabilities, so "execute a query" can take many different forms. In the case of EHQL, what it means is to execute a [DataFrame query] over [Spark Connect].

[DataFrame query]: https://spark.apache.org/docs/4.1.2/sql-programming-guide.html
[Spark Connect]: https://spark.apache.org/docs/4.1.2/spark-connect-overview.html

### Drafting a Target Solution

In my post on designing EHQL, I worked through a [reference query in SQL][ehql-sql]. Spark can execute SQL just fine, but I prefer to work with its DataFrame API. The main reason is that the DataFrame API is accessible in the host language -- in our case, Python -- so you get proper IDE support like you would with any library, rather than having to embed or awkwardly build SQL strings. The DataFrame API also offers a [fluent interface] that makes it natural to chain operations, similar to how you would in Unix shell programming.[^pipe] Spark compiles both SQL and DataFrame queries down into the same intermediate representation and executes them with exactly the same optimizer, so there is no runtime difference between using these different interfaces. It's all about what interface works better for your use case.

[^pipe]: The broader data industry has recognized the value of this style of API for data processing, and that has led to the introduction of "pipelined" versions of SQL like [Pipe SQL] and [PRQL].

[ehql-sql]: {% post_url 2026-05-11-custom-query-language-design %}#first-stop-sql
[fluent interface]: https://martinfowler.com/bliki/FluentInterface.html
[Pipe SQL]: https://spark.apache.org/docs/latest/sql-pipe-syntax.html
[PRQL]: https://prql-lang.org

Another key decision I made was to use [Spark Connect] to interface with Spark, rather than the classic DataFrame API. The DataFrame API is the same, but the difference is that whereas before your client would need to run a fairly heavy JVM to host the driver for your Spark application, with Spark Connect your client becomes a thin and lightweight layer that doesn't know anything about the JVM or Spark's internals. Instead, it just builds logical query plans from your DataFrame queries, sends them over the wire to the server, and gets the results back. This makes working with the DataFrame API much more like working with SQL in that your client is fully decoupled from the server and can be embedded basically anywhere, even on the most resource-constrained hosts.[^langs] And the best part is that using Spark Connect doesn't require anything other than a small change in how we initially connect to Spark, as you'll see just below.

[^langs]: This is also why there has been an explosion of support for different languages to run Spark's DataFrame API---[Swift], [Rust], [Go], [JavaScript], [C++], and [.NET]. They don't need to deal with the JVM as before.

[Swift]: https://github.com/apache/spark-connect-swift
[Rust]: https://github.com/apache/spark-connect-rust
[Go]: https://github.com/apache/spark-connect-go
[JavaScript]: https://github.com/yaooqinn/spark.js
[C++]: https://github.com/irfanghat/spark-connect-cpp
[.NET]: https://github.com/GoEddie/spark-connect-dotnet

What does our query look like in the DataFrame API? Porting [the original SQL][ehql-sql] from my previous post is pretty straightforward.

```python
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, bool_or


# `.remote()` is what creates the session using Spark Connect.
spark = SparkSession.builder.remote("local[*]").getOrCreate()

(
    spark.table("maintenance_history")
    .withColumns({
        "has_oil_change": col("work_done") == "oil change",
        "has_tx_fluid_change": col("work_done") == "transmission fluid change",
    })
    .groupBy("vehicle_id")
    .agg(
        bool_or("has_oil_change").alias("has_oil_change"),
        bool_or("has_tx_fluid_change").alias("has_tx_fluid_change"),
    )
    .where(
        col("has_oil_change")
        & col("has_tx_fluid_change")
    )
)
```

This, roughly, is what we want to convert the above parse tree into.

### Choosing the Right Lark Visitor

Lark offers a [few different ways][visit] of transforming your parse tree into its final form. These ways differ on two different axes:

[visit]: https://lark-parser.readthedocs.io/en/latest/visitors.html

- They process the tree top down vs. bottom up.
- They reconstruct the tree vs. edit it in place.

Our language skeleton is simple enough that it makes most sense to use Lark's [`Transformer`], which processes the tree bottom up, building a new tree as it does so. If we were implementing more of EHQL's design we might want to process the tree differently. Consider [event aliases]. Since aliases used in one part of a query are defined in another part, we may need to process the tree top down or even in multiple passes to build our final query.

[`Transformer`]: https://lark-parser.readthedocs.io/en/latest/visitors.html#transformer
[event aliases]: {% post_url 2026-05-11-custom-query-language-design %}#unfinished-ideas

<!--
To illustrate, consider one of the [unfinished ideas] I presented: Lookback Periods.

[unfinished ideas]: {% post_url 2026-05-11-custom-query-language-design %}#unfinished-ideas

```sql
history contains:
  within past 2 years:
    "oil change"
```

The parse tree for this query might look something like this:

```
history_clause
  lookback_window "2 years"
    history_pattern
      event_name  "oil change"
```

If we process the tree bottom up, we'll get to the `"oil change"` event before we realize it's scoped to the past two years. Depending on how exactly we build our query, this isn't necessarily a problem, but we might want to process the tree top down first to push contextual constraints like our lookback period down into the history patterns they apply to, before then processing the tree again bottom up as we normally would. But we don't need to get into these decisions now; a single pass from the bottom up is sufficient for our purposes. Just know that in other cases you may want to process your parse tree in multiple passes and multiple directions, depending on the complexity of your language and how you build your final query.
-->

### Building the Query Bit by Bit

#### Step 1: `event_name`

Let's start with a very simple implementation to show you how a `Transformer` works in practice.

```python
from lark import Transformer, v_args


class EvaluateEHQLSkeleton(Transformer):
    @v_args(inline=True)
    def event_name(self, quoted_string: str):
        return quoted_string.strip('"')


transformed_tree = EvaluateEHQLSkeleton().transform(parse_tree)

print(parse_tree.pretty())
print(transformed_tree.pretty())
```

This will print the initial parse tree followed by the result of transforming that tree using the `EvaluateEHQLSkeleton` transformer.

```
history_clause
  history_pattern
    event_name  "oil change"
  history_pattern
    event_name  "transmission fluid change"

history_clause
  history_pattern       oil change
  history_pattern       transmission fluid change
```

There are a few things going on here -- and this is a good snippet to tinker with if you are following along -- but the most important to call out are the following:

- The `event_name` method in our transformer is automatically matched by Lark to the corresponding grammar rule, which is very neat. Since we didn't define methods for the other rules, the corresponding tree nodes remain unchanged.
- Note how our `event_name` method strips the double quotes from each event name. We get a new tree with those nodes replaced by the output of that method.
- The `v_args(inline=True)` decorator simplifies the logic of our method a bit since normally it would take as input a list of the node's children. Since we know the `event_name` node has exactly one child -- the quoted string capturing the event name -- the decorator simplifies the method's interface so we can accept the string directly.

#### Step 2: `history_pattern`

Let's go to the next level up our parse tree by filling in a definition for `history_pattern`:

```python
import pyspark.sql.functions as sqlf
from lark import Transformer, v_args


class EvaluateEHQLSkeleton(Transformer):
    @v_args(inline=True)
    def history_pattern(self, event_name: str):
        event_name_slug = event_name.lower().replace(" ", "_")
        condition_name = f"__has_{event_name_slug}"
        return (
            condition_name,
            sqlf.col("work_done") == event_name,
        )

    # Include methods from previous steps.
    ...
```

Running the transformation with this updated definition now gives us this before and after of our parse tree:

```
history_clause
  history_pattern
    event_name  "oil change"
  history_pattern
    event_name  "transmission fluid change"

history_clause
  ('__has_oil_change', Column<'==(work_done, oil change)'>)
  ('__has_transmission_fluid_change', Column<'==(work_done, transmission fluid change)'>)
```

Do you see how the parse tree is slowly turning into a set of Spark data structures? All we did in our `history_pattern` rule is create Spark column expressions for each of the maintenance events in our query, and we paired those with aliases in a plain Python tuple. One more step and we'll have the DataFrame that we want.

#### Step 3: `history_clause`

Now we can piece everything together at the root of the tree and build our final query:

```python
import functools
import operator
import pyspark.sql.functions as sqlf
from lark import Transformer, v_args


class EvaluateEHQLSkeleton(Transformer):
    def __init__(self, spark):
        self.spark = spark

    # We don't use `v_args` here because there are a variable number of
    # patterns and it's best to accept them as a list.
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

    # Include methods from previous steps.
    ...


result = EvaluateEHQLSkeleton(spark).transform(parse_tree)
# The result is now a DataFrame, so we call `.show()` instead of `.pretty()`.
result.show(truncate=False)
```

This final step is a bit long because this is where we piece everything together into the final result, but it should be easy to follow as it maps directly to the [original SQL I wrote][ehql-sql] for this use case.

We've fully transformed the original parse tree into a DataFrame query that returns the vehicles we're looking for:

```
+----------+----------------+-------------------------------+
|vehicle_id|__has_oil_change|__has_transmission_fluid_change|
+----------+----------------+-------------------------------+
|3344      |true            |true                           |
+----------+----------------+-------------------------------+
```

And that's it! We have a working skeleton implementation of EHQL that runs simple queries by converting them into DataFrames and running them on Spark.

## Building out the Rest of EHQL

EHQL has a lot of features that we didn't cover here. To implement them, we'd need to add the appropriate rules to our grammar; for each rule, we'd need a matching implementation in our Lark visitor that describes how to convert the parsed tokens returned by that rule into the right Spark code. It may not be possible or desirable to keep the implementation in a single `Transformer` class; we may need multiple passes to build the right query, or we may want to first build an abstract syntax tree (AST) before converting it into the final query. An AST would more directly capture the _semantic_ structure of a query versus the _grammatical_ structure returned by the parse tree. For our skeleton of EHQL, those two things are very similar, but as we build out more of the language it may make more sense to construct a proper AST before building the final DataFrame.

I hope that this walk through of [designing][prev] and implementing EHQL inspires you to try your hand at making your own custom query language. I may try to build out more of EHQL and release it as a proper demo project. But for now, here is a [standalone script][script] that captures everything we walked through in this post. [Try it out!][script]

[script]: /assets/query-language-implementation/code/ehql.py
