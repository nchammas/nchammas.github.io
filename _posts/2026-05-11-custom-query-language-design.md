---
layout: post
title: "Designing a Custom Query Language for Non-Technical Analysts"
permalink: /writing/:title
tags: [dsl, query-language]
---

I recently had the pleasure of designing and implementing a custom query language[^dsl], along with an integrated execution environment. It was my first time doing anything like this, and it became a passion project I dedicated many nights and weekends to. I learned more working on this side project on and off over the course of about 18 months than I did at my day job in the past eight years.

[^dsl]: More generally known as a _domain-specific language_, or DSL.

I can't share the exact project since it was in service of a group at my then-employer. But in this post I will walk through a broadly equivalent hypothetical scenario and the design process I used: mapping out the problem, trying existing solutions, and finally designing my own query language.


## Understanding the Need

First, why would you want to design a custom query language? There are many possible reasons; in my case, the decision to take this on emerged from an understanding of three things:

1. The user base I'm trying to help.
2. The data these users want to query.
3. These users' use cases.

I imagine this combination of factors is what drives the creation of most query languages.

### User Base: Analysts

Our target user base is non- and semi-technical analysts specializing in vehicle maintenance data.

The non-technical analysts may have experience with something like Excel and may know how to write basic formulas for that system, but they are not familiar with SQL or programming otherwise.

<a id="semi-technical-analyst"/>
The semi-technical analysts know a fair amount of SQL but are not professional software developers. That is, they know how to write basic queries that involve joins or aggregates, but they're not as comfortable using more advanced techniques like window functions or correlated subqueries.

### Data: Vehicle Maintenance Logs

The data these users are interested in is vehicle maintenance data being streamed into a central database. The data is updated daily and spans 10+ years, covering 100 million vehicles across the United States.

While the implementation details of how exactly the data is stored[^stored] are interesting -- the format, the storage system, partitioning scheme, etc. -- what matters most for our purposes is the logical or abstract schema of the data.

[^stored]: Briefly: The data was on the order of 10-100 TB in size, stored as Parquet and managed by Apache Spark. These details don't matter so much for our design discussion here, but they did impact my search for existing solutions.

This data can be described abstractly as two tables with the following schemata:

```
vehicle
    vehicle_id
    make
    model
    year

maintenance_history
    vehicle_id
    time
    work_done
    detail
```

Example "rows" from each table:

`vehicle`:

| vehicle_id | make   | model   | year |
|------------|--------|---------|------|
| 224        | Volvo  | XC60    | 2017 |
| 7889       | Mazda  | Mazda3  | 2009 |
| 8031       | Toyota | Tacoma  | 2007 |

`maintenance_history`:

| vehicle_id |         time           |         work_done           |       detail       |
|------------|------------------------|-----------------------------|--------------------|
|    224     | 2023-10-13 14:33:17    | oil change                  | 0W-20              |
|    224     | 2023-10-13 14:50:09    | oil filter change           | _NULL_             |
|   7889     | 2010-01-03 09:11:42    | timing belt replacement     | _NULL_             |
|   8031     | 2015-08-30 12:03:31    | diagnostic                  | P1155              |

### Use Case: Defining Vehicle Populations

Our target user base is interested in finding vehicles that fit a specific profile and maintenance history, mainly to market products or services to the owners of those vehicles. A common industry term for this is [vehicle population][vp].

[vp]: https://www.nhtsa.gov/search?q=vehicle+population

<!--
Query types:
1. Vehicle History
2. Vehicle Attributes + History
3. Temporal Relationship within History
-->

Here are some example vehicle populations:
1. All vehicles that have had an intake upgrade, exhaust upgrade, and their fuel/air ratio reconfigured (i.e. [rice rockets][rice]).
2. Toyotas that threw a P1155 diagnostic code three or more times but have not had an oxygen sensor replaced yet.
3. Vehicles that had a transmission fluid change and then, within six months, had a full transmission rebuild.

[rice]: https://www.youtube.com/watch?v=f9x74SlY1ik

There are many more use cases I brainstormed for my target user base. However, wanting to constrain and focus my design, I distilled them down to the above small set of example cases that I felt best captured my users' needs. These are the _reference queries_ that I used to guide my design process.

I don't know if this is how query languages are typically designed. But I can say that this process of profiling my users and distilling their needs into a focused set of queries was the most important exercise I did before designing the language. It grounded my work and gave me something to test design ideas against.


## Is there an Existing Solution?

This can't be the first time someone has encountered a problem like this. Maybe there's something out there we can just pick up off the shelf and use.

### First Stop: SQL

My natural -- and correct, in my humble opinion -- impulse was first to wonder if I could just use SQL. The data is already structured as tables. And SQL is a robust, flexible query language that is widely used and has stood the test of time. Perhaps all I needed to do was create a few views to make certain query types easier to express and that would be good enough.

So here I worked through another exercise which I found to be just as critical to my design process as the previous exercise: I expressed my reference queries in SQL to see what they would look like. Was there a reusable pattern to these queries? Were the queries themselves simple enough to encapsulate somehow so that our semi- and non-technical users could write them? Ultimately, do I really need to bother developing a custom query language or not?

<!-- ### Querying a Vehicle's History -->

Consider the first reference query we documented earlier:

> All vehicles that have had an intake upgrade, exhaust upgrade, and their fuel/air ratio reconfigured.

This is a query for vehicles of any type that have three separate events in their maintenance history. The events can be in any order; we just want to see that all three are present for a given vehicle in order to return it in the result set for this query. A large chunk of my users' queries -- about half -- are roughly of this nature, so any solution I came up with needed to work really well for this simple use case.

My first attempt at writing this in SQL looked something like this:

<a id="sql-first-attempt"/>

```sql
SELECT vehicle_id
FROM maintenance_history
WHERE
        work_done = 'intake upgrade'
    AND work_done = 'exhaust upgrade'
    AND work_done = 'fuel/air ratio reconfig'
;
```

This query is wrong, but it's the kind of query I _want_ to be able to write to satisfy this use case. It's wrong because we're looking for three _separate_ rows with the same `vehicle_id` that have these three different values for `work_done`, whereas the above query tries to find a _single_ row that matches all values, which obviously won't work. Changing the `AND`s to `OR`s won't help either because that will find vehicles that have _any_ of these events in their maintenance history, whereas we want vehicles that have _all_ of them.

This process of fumbling through what I _feel_ like I want to be able to write turned out to be another key in helping me design a better solution. We'll come back to this point later.

A correct SQL query for this use case would be:

```sql
SELECT vehicle_id
FROM maintenance_history
WHERE work_done IN (
    'intake upgrade',
    'exhaust upgrade',
    'fuel/air ratio reconfig'
)
GROUP BY vehicle_id
HAVING COUNT(DISTINCT work_done) = 3
```

This works but it's inflexible. If we slightly change our query to search for cars that upgraded _either_ their intake or exhaust (or both), in addition to reconfiguring their fuel/air ratio, we can't use this query pattern anymore. That's because the aggregation, as it is structured here, destroys the information about what specific work was done on the vehicle.

We want to aggregate each vehicle's maintenance history so we can query across the entire history at once, without losing the detail that enables us to express finer conditions like "had this _or_ that" or "had this _and then_ that".

After some experimentation, this is the query pattern I came up with:

```sql
WITH history AS (
    SELECT *,
        work_done = 'intake upgrade' AS has_intake_upgrade,
        work_done = 'exhaust upgrade' AS has_exhaust_upgrade,
        work_done = 'fuel/air ratio reconfig' AS has_ratio_reconfig
    FROM maintenance_history
    -- This filter here is important for performance but is not required
    -- for the correct result.
    WHERE work_done IN (
        'intake upgrade', 'exhaust upgrade', 'fuel/air ratio reconfig'
    )
)
SELECT
    vehicle_id,
    BOOL_OR(has_intake_upgrade) AS has_intake_upgrade,
    BOOL_OR(has_exhaust_upgrade) AS has_exhaust_upgrade,
    BOOL_OR(has_ratio_reconfig) AS has_ratio_reconfig
FROM history
GROUP BY vehicle_id
HAVING
        has_intake_upgrade
    AND has_exhaust_upgrade
    AND has_ratio_reconfig
```

Obviously, this solution is lengthier than the previous one. But as a query pattern it breaks our query down into two logical stages: first, we extract the key attributes of each vehicle's history (the various `BOOL_OR(...)` parts), and then we filter on those attributes (in the `HAVING` clause). This separation makes the pattern quite flexible.

<a id="first-query-variation"/>
For example, let's revisit the variation on the first query that we considered:

> cars that upgraded either their intake or exhaust (or both), in addition to reconfiguring their fuel/air ratio

This can be expressed with largely the same query as above. All we have to change is the `HAVING` clause:

```sql
HAVING
        (has_intake_upgrade OR has_exhaust_upgrade)
    AND has_ratio_reconfig
```

We've worked through only the first reference query, but it's already striking how verbose and cumbersome our SQL solution is compared to the plain English description of our target vehicle population. Other queries may have aspects that don't cleanly fit the pattern we came up with, and more complex queries will of course require even lengthier SQL to express.

While I worked through translating about a half dozen queries into SQL as part of my exploration of the problem, I won't go through that all here. But my conclusion was that I couldn't see how I would simplify this for users who would find this amount of SQL intimidating or downright inaccessible. Each query needs to extract different attributes from the maintenance history, so somehow restructuring the data in a set of views to make queries simpler doesn't seem like a viable approach.

{% comment %}
<!--
### Querying a Vehicle's Attributes + More Complex History

We've expressed our first reference query or vehicle population as SQL, and the exercise so far is already quite illuminating. Since each reference query captures unique aspects of our users' needs, let's continue with the next query.

Our second reference query is:

> Toyotas that threw a P1155 diagnostic code three or more times but have not had an oxygen sensor replaced yet.

Compared to the first query, this one introduces a few new concepts:

1. A filter on a vehicle's attributes, not just its maintenance history.
2. A minimum count of a specific maintenance event, not just checking for its presence.
3. Requiring the absence of a specific event from the maintenance history.

Starting with the query pattern we developed for the first reference query, let's extend or enhance it to support this second query and see what the result looks like.

To filter down to a specific vehicle make, we'll obviously need to join to `vehicle`. And to check for the absence of a specific maintenance event, we can still use our `has_this_event` pattern from before; we'll just need to check for `NOT has_this_event` in the `HAVING` clause. This is pretty straightforward so far, and it's a good sign that our query pattern can support these concepts without needing to change significantly.

That leaves the minimum count, which is more interesting. Let's apply the two logical query stages we developed in the previous section to this problem: first, extract the key attribute of the maintenance history that we need, and then filter on it. The key attribute is a count of the target maintenance event (so a `COUNT(...)` instead of `BOOL_OR(...)`), and the filter on that attribute is the minimum count (i.e. `HAVING event_count >= 3`).

Putting it all together:

```sql
WITH history AS (
    SELECT *,
        work_done = 'diagnostic' AND detail = 'P1155' AS has_p1155,
        work_done = 'o2 sensor replaced' AS has_o2_sensor_replaced
    FROM maintenance_history
)
SELECT
    h.vehicle_id,
    COUNT_IF(has_p1155) AS p1155_count,
    BOOL_OR(has_o2_sensor_replaced) AS has_o2_sensor_replaced
FROM
    vehicle v
    INNER JOIN history h
        ON v.vehicle_id = h.vehicle_id
WHERE make = 'Toyota'
GROUP BY h.vehicle_id
HAVING
    p1155_count >= 3
    AND NOT has_o2_sensor_replaced
```

This works, but it's striking how verbose and cumbersome our SQL solution is compared to the plain English description of our target vehicle population. And I don't see how I would simplify this for users who would find this amount of SQL intimidating or downright inaccessible. Each query needs to extract different attributes from the maintenance history, so somehow restructuring the data in a view (or set of views) to make queries simpler doesn't seem like a viable approach.

### Querying Temporal Relationships Between Maintenance Events

It's already clear at this point in our exercise that SQL is not an appropriate solution for our target user base. Even the semi-technical users who know how to write some SQL would find the queries we have written so far to be, at best, cumbersome to read and write day in and day out.

For the sake of completeness, however, let's express our third and final reference query in SQL. It's a very different type of query, and working through it will likely inform our design process for this use case.

The third reference query is:

> Vehicles that had a transmission fluid change and then, within six months, had a full transmission rebuild.

What's interesting about this query is that we're looking for events within a vehicle's maintenance history that have a specific temporal relationship to one another: One event happens after the other, and there is a specific time gap between the two events.

The most direct way to do something like this in SQL is to join `maintenance_history` to itself and express the temporal relationship in the join condition.

```sql
SELECT
    c.vehicle_id,
    c.time AS tx_change_time,
    r.time AS tx_rebuild_time,
    DATEDIFF(r.time, c.time) AS gap_days
FROM maintenance_history c
    INNER JOIN maintenance_history r
        ON  c.vehicle_id = r.vehicle_id
        AND DATEDIFF(r.time, c.time) <= (6 * 30)
WHERE
    c.work_done = 'transmission fluid change'
    AND r.work_done = 'transmission rebuild'
```

This works well but it doesn't fit our previous pattern of extracting attributes from the history as part of a `GROUP BY` and then filtering on those attributes. If our query included a combination of attributes like in our previous queries as well as some temporal relationships, we'd probably want to massage this solution to fit the previous pattern or integrate smoothly with it so we have a repeatable way to express any of our queries in SQL.

---

SQL implementation notes

There are other approaches I considered but rejected:
- We could group by vehicle and collect the maintenance events into an array, and then check the array for the presence, absence, ordering, etc. of events. I didn't like this because querying arrays like this in SQL is awkward and, depending on the query, the arrays themselves may end up quite large and impose some kind of performance problem. Heavy use of arrays in this fashion seemed "spiritually" at odds with SQL's strengths, so I was discouraged from exploring this further.[^2]
- We could write a separate query for each filter condition -- one for the intake upgrade, another for the exhaust upgrade, etc. -- and intersect, union, or otherwise combine them all as the top-level query demanded. I didn't like this because it felt cumbersome and I couldn't immediately imagine how I would express a more complex query like "did this _and then_ did that". I'm not saying it's not possible; just that there seemed to be too much friction to applying the idea to my reference queries.

[^2]: There was a fair amount of experimentation and exploration of ideas that was guided in this fashion by my instincts or gut feelings, rather than some kind of hard facts or test results. It felt natural to me and, not having any evidence to the contrary, I assume a lot of productive design in the world happens in this way.
-->
{% endcomment %}

### Why not use an LLM?

So the SQL is lengthy. So what? LLMs are very good at writing code for constrained problems like this. We could just give an LLM the plain English descriptions of the vehicle populations we want and let it build the SQL automatically. It's an attractive idea. For some, an LLM and a thin interface are a good enough replacement for a custom query language. For this situation, however, LLMs don't work as a complete solution.

Natural English doesn't have the precision of a programming language, so an LLM first has to interpret potentially ambiguous descriptions. Did it interpret the description correctly? Someone has to validate the generated SQL. As we saw in our walkthrough of the first reference query, the SQL is much more verbose than the English description it captures. And many queries, like our other two reference queries, will be more complex than that.

Over the course of a year, these analysts write hundreds -- perhaps even thousands -- of queries describing vehicle populations. These populations are designed based on the analysts' business knowledge and conversations with clients. The analysts need to understand and defend the details of each population they build. How can they do that if they cannot understand the SQL being generated? Even the semi-technical analysts who do know some SQL would quickly fatigue from having to review so much generated SQL day in and day out.

In other words, the problem is mainly one of [accountability][ibm] and accessibility. With an accessible query language tailored to the business problem at hand, these analysts can own their work. A custom language would also make LLMs much more useful to them than if they were working with SQL. They'd be able to generate and debug queries that they can actually understand. And with a query language that's much more constrained than SQL, LLMs are much more likely to get translations to and from English correct on the first try.

LLMs don't change this fundamental fact: Programming is thinking, and thinking clearly is much easier when you're working with the right abstractions. Our analysts, whether they think of themselves as such or not, are doing a form of programming.

[ibm]: https://x.com/bumblebike/status/832394003492564993/photo/1

### Other Prior Art

I did two rounds of research into prior art. The first took place before I started work on my design in late 2024. At the time I wasn't sure what to look for or how to explain the problem in a domain-agnostic way that wasn't tied to the topic of vehicle maintenance. I was already familiar with [Cypher][cypher] and [GraphFrames][gf]. Though my problem wasn't a graph problem, I did take inspiration from their syntaxes even if I didn't use either directly.

[cypher]: https://neo4j.com/docs/cypher-manual/current/introduction/cypher-overview/
[gf]: https://graphframes.io

I remember searching for "time series analysis" and "time series query language", as I thought that was the closest description to what I was doing. But the results seemed to have a focus on statistical analysis rather than pattern matching, which I would later understand was a better description of what I wanted. One interesting find from this round of searching was [Kusto][kusto], whose first-class pipelining reminds me of [PRQL][prql].

[kusto]: https://learn.microsoft.com/en-us/kusto/query/
[prql]: https://prql-lang.org

In the course of writing this post I did another round of prior art research, this time guided by my experience designing and implementing my new language, and also using the assistance of an LLM. I found more relevant results this time, first among them being the term [Complex Event Processing][cep] (CEP), which seems to be an industry term that best describes our problem domain. There are a lot of CEP engines out there. Some of them are proprietary; most extend SQL or offer a DSL that is very SQL-like.

[cep]: https://en.wikipedia.org/wiki/Complex_event_processing

A surprising find in this area was SQL's [`MATCH_RECOGNIZE`][mr], which had somehow escaped my notice before. Oracle first released this feature in 2013 and it was later standardized as part of SQL:2016. This seems to be SQL's solution to the problem of event pattern matching. It's not widely supported as of early 2026 (including neither by PostgreSQL nor by Apache Spark) and, most importantly, it doesn't simplify the expression of our reference queries enough to make them accessible to my users. But it's an interesting development nonetheless!

[mr]: https://learn.microsoft.com/en-us/stream-analytics-query/match-recognize-stream-analytics

The most interesting find I made while researching CEP engines for this post is Elasticsearch's [Event Query Language (EQL)][eql]. It's designed primarily for threat detection, but for some of our reference queries it seems to fit the problem quite well. If my company was already using Elasticsearch it would have been worth digging a bit into EQL as a solution, or at least as a starting point for one.

[eql]: https://www.elastic.co/docs/explore-analyze/query-filter/languages/eql

<!--
```sql
-- first reference query in EQL
sample by vehicle_id
  [ any where work_done == "intake upgrade" ]
  [ any where work_done == "exhaust upgrade" ]
  [ any where work_done == "fuel/air ratio reconfig" ]
```
-->

The last bit of prior art I want to make note of is [Logica][logica]. It's not a CEP engine, but rather a logic programming language in the Datalog family. What always impresses me about Datalog is how elegantly it expresses ideas that take much more prose to replicate in SQL. And what's special about Logica in particular is that it compiles programs into SQL that you can run at scale (though Apache Spark is currently not one of its supported dialects). If I were starting over today, I would look into the possibility of building a thin wrapper around Logica as the DSL for my users.

[logica]: https://logica.dev


## Distilling the Essence of the Problem

In looking for an existing solution to the problem I was trying to solve for my intended users, I started in the right place: SQL, the _de facto_ universal query language. And while at the time I took on this work I did not have the insight that I was looking for a CEP engine (or something like that), the process of talking to users and implementing their examples in SQL gave me insight into the "essence" of the queries. It wasn't something that hit me suddenly like an "a-ha!" moment; it was more like seeing a shape emerge from the mist and progressively get clearer until you could tell what it was.

<div style="text-align: center;">
<figure>
    <span>
        <img
            src="/assets/query-language-design/vehicle.svg"
            width="500"
        />
    </span>
    <figcaption>
    A visual data abstraction of a collection of vehicles.
    </figcaption>
</figure>
</div>

The analysts are interested in querying vehicle maintenance data on two different axes: one is the vehicle itself---that is, its properties like make, model, and so forth; the other is the vehicle's maintenance history, which is a timeline of maintenance events like "oil change on this date", "tire rotation on that date", and so on.

The underlying representation of the data is, in fact, a separate matter from this abstract model of how the analysts think about their domain and phrase their queries. The closer the new query language tracks this abstract model, independent of the underlying data storage picture, the more natural analysts will find it.

Of course, this type of problem is not unique to vehicle maintenance data. Anything that has a history could fit this model: sensors that take measurements (e.g. temperature, humidity, etc.), people making purchases, even volcanos erupting. The key is that we have an _entity_ which has some kind of _history_.

<div style="text-align: center;">
<figure>
    <span>
        <img
            src="/assets/query-language-design/entity.svg"
            width="500"
        />
    </span>
    <figcaption>
    A generalized version of the data structure we are querying.
    </figcaption>
</figure>
</div>

The analysts' queries against a vehicle's history look for a combination of events or event patterns _across the entire history_. This is also key, as other query languages may facilitate querying for individual events or querying across multiple entities, which isn't the focus here.

As I thought about how best to express the event patterns these users wrote, I found it useful to sketch out maintenance histories on a timeline. When I sketched this out on paper, the image that came to mind was that of overlaying pattern strips on the timeline to see if the timeline _as a whole_ matched what the query was looking for.

<a id="pattern-overlay" />

<div style="text-align: center;">
<figure>
    <span>
        <img
            src="/assets/query-language-design/history-pattern.svg"
            width="600"
        />
    </span>
    <figcaption>
    An event pattern overlay representing a transmission rebuild within six months after a transmission fluid change.
    </figcaption>
</figure>
</div>

<div style="text-align: center;">
<figure>
    <span>
        <img
            src="/assets/query-language-design/pattern-match.svg"
            width="600"
        />
    </span>
    <figcaption>
    A vehicle with this maintenance history will be returned by the query because it matches our event pattern.
    </figcaption>
</figure>
</div>


The physicality of this image synthesized so many thoughts and ideas -- ranging from fuzzy to precise -- into a concrete picture for me. It also gave me inspiration for a suitable syntax, which I'll get to later in this post.

Before that, we must first carry out that most enjoyable of tasks when creating something new, and give our new query language a name. The obvious choice is Vehicle Query Language (VQL), but a more general name that captures the core abstractions and query patterns would be Entity History Query Language (EHQL).


## Designing the Syntax

At this point I felt I had a good command of the problem, having worked through a clear problem description, reference queries, example implementations of some of those queries, and an abstract data structure. It was a lot of prep work, but at this point I was finally ready to design the syntax itself.

The central problem of designing a query syntax was coming up with something approachable for my semi- and non-technical users, while still offering something expressive and precise enough to cover their typical query needs. The plain English of the reference queries was already quite good---expressive, easy to understand, and well-suited to the domain of describing vehicle populations. It's also much more concise than the equivalent SQL we had to write. What it lacked was the structure and precision of a programming language.

So the problem becomes: Starting with the plain English queries, can we add just enough structure to make them precise, while preserving the clarity and ease-of-use? Well, my [first attempt](#sql-first-attempt) at writing an SQL query got close to capturing exactly that. So I stripped it down a bit:

```sql
-- Reference Query 1: All vehicles that have had an intake upgrade,
--   exhaust upgrade, and their fuel/air ratio reconfigured.
maintenance_history WHERE
        work_done = 'intake upgrade'
    AND work_done = 'exhaust upgrade'
    AND work_done = 'fuel/air ratio reconfig'
```

Since we're designing for a narrow use case, we don't need SQL's generality; every degree of freedom is a degree of complexity. Instead of general tables and relations, our query language only needs to interact with two data abstractions, which we can elevate into keywords: `vehicle` and `maintenance_history`. In this case each abstraction does happen to map to an underlying table of data, but this is not directly relevant to the design of the query language. The language is expressing filters on data abstractions which are decoupled from whatever underlying storage implementation we have. When we come to implement our language we'll have to bridge this gap between data abstraction and concrete storage, but that's for later.

Finally, we can just drop the entire `SELECT` clause since we are just defining vehicle populations and each query would return a set of distinct vehicle IDs. If our language needed the ability to select specific vehicle attributes, or perhaps even select the events that matched the specified history patterns (which is an interesting problem), then we would need some syntax to express that. Saying "no" to this capability for now makes our design job easier, and we may end up [never needing this capability anyway][yagni].

[yagni]: https://c2.com/xp/YouArentGonnaNeedIt.html

### Significant Indentation

If my users could write a query like this, that would already be a big win. But I wanted to simplify this further based on an important piece of feedback I had gotten from users about their experience working with SQL and other custom query languages at the company: They _hated_ interacting with long or deeply nested lists of conditions or values. These lists demanded careful placement or balancing of binary operators, commas, and parentheses. One user, a [semi-technical analyst](#semi-technical-analyst), commenting on a fairly lengthy query that required a lot of perfectly balanced parentheses, called it "disgusting".

This is where I thought to borrow the most distinctive feature of Python: significant indentation. The indentation would group conditions or values in a clear visual block that was easier to read and required less fidgety syntax to get right.

```sql
-- Reference Query 1: All vehicles that have had an intake upgrade,
--   exhaust upgrade, and their fuel/air ratio reconfigured.
history contains:
  work_done = "intake upgrade"
  work_done = "exhaust upgrade"
  work_done = "fuel/air ratio reconfig"
```

I made some other changes that seemed natural to me:
- Just `history`, which is more concise but still clear from the context that it refers to the maintenance history.
- `contains` instead of `where` since we are searching across the entire history and the former captures the semantics of the filter more accurately.
- Double quotes for string literals instead of single quotes, since that's more common. I considered allowing both, and if users found this choice to be a sticking point I could revisit it. But it's better to have a single way to do this.

Instead of significant indentation, I considered going with bulleted lists:

```sql
history contains:
  - work_done = "intake upgrade"
  - work_done = "exhaust upgrade"
  - work_done = "fuel/air ratio reconfig"
```

This would probably also be very accessible to my users as it leverages a common document formatting idiom, but the bullets add visual noise and my users and I liked the pure whitespace version better.

One reasonable concern about significant indentation for a custom query language is that queries are likely to be embedded in another language that does not have significant indentation. [The argument][go-indent] is that this leads to bugs over time as the indentation is changed in the surrounding language, which subtly changes the semantics of the embedded, indentation-sensitive query. This concern doesn't apply in our case since the query language was designed with its own execution environment in mind and designed specifically for non-programmers.

[go-indent]: https://go.dev/talks/2012/splash.article#:~:text=we%20have%20had%20extensive%20experience%20tracking%20down%20build%20and%20test%20failures%20caused%20by%20cross%2Dlanguage%20builds%20where%20a%20Python%20snippet%20embedded%20in%20another%20language

### Against Boolean Operators

Alright, so our new language is starting to take shape. Let's consider the [variation on our first reference query](#first-query-variation), where two conditions are or-ed together. How do we do that with significant indentation? Do we need to reintroduce the parentheses and boolean operators that our users complained about? Not quite.

Continuing a theme, I adopted Python's `all` and `any` [built-in functions][py-all].

[py-all]: https://docs.python.org/3/library/functions.html#all

```sql
history contains:
  all of:
    work_done = "fuel/air ratio reconfig"
    any of:
      work_done = "intake upgrade"
      work_done = "exhaust upgrade"
```

This is surprisingly readable, and we can make it a bit more concise without losing any readability by making the `of` optional. More significantly, we can also make `all` the default, top-level group condition, since that naturally matches the most common use cases, allowing users to just drop that part entirely.

```sql
-- same semantics as the query just above
history contains:
  work_done = "fuel/air ratio reconfig"
  any:
    work_done = "intake upgrade"
    work_done = "exhaust upgrade"
```

### Simple Cases Should be Simple

Recall that by far our most common use case is to just list some number of events that must be present somewhere in the history. We really want to make this use case as pleasant as possible without marring the language in more complex cases. I thought it would be best to just drop the `work_done`, knowing that this is typically what users are searching on.

```sql
-- still the same semantics
history contains:
  "fuel/air ratio reconfig"
  any:
    "intake upgrade"
    "exhaust upgrade"
```

And if users need to specify a filter on `detail` in addition to `work_done`, we can extend this idea by borrowing the hierarchical notation of filesystem paths.

```sql
history contains:
  "diagnostic" / "P1155"
  "oil change" / "0W-20"
```

Each condition in the above query expresses an equality filter on `work_done` and `detail`, respectively, which are the two attributes users are filtering on the vast majority of the time. Users really liked the conciseness of this syntax. Simple queries have simple expressions in our language, and that's a good sign.

But what if users want to use something more complex to describe events, like additional attributes, or comparison operators like `in` or `like`, which some of them would know from their exposure to SQL? It's tempting to reintroduce the parentheses and boolean operators we have so far avoided.

```sql
history contains:
  work_done = "oil change" and detail like "0W-%"
  work_done = "tire replacement" and (detail = "Bridgestone" or detail = "Michelin")
```

This is not so bad. The use is limited enough that users are unlikely to get to "disgusting" levels of nesting or balancing syntax in this way. But we could perhaps still avoid having to allow this type of syntax back in by further leveraging blocks.

```sql
history contains:
  event:
    work_done = "oil change"
    detail like "OW-%"
  event:
    work_done = "tire replacement"
    any:
      detail = "Bridgestone"
      detail = "Michelin"
```

I think this is better and more consistent with the "spirit" of the new language that is taking shape.

### More Complex Filters

Moving on to the second reference query, there were several new filter types that the language needed to support: vehicle attributes, event counts, and the absence of events.

The vehicle attributes seemed like the easiest to address using all of the design choices I'd already made so far. They are separate from the maintenance history and so should get their own top-level block. Otherwise, I could reuse the rest of the syntax I'd already come up with.

```sql
vehicle has:
  make = "Toyota"
  -- extra filters just for demonstration purposes
  model = "Camry"
  year >= 2015
history contains:
  ...
```

If we wanted to decouple the language from vehicle maintenance records, `entity has` would be more appropriate. The choice of `has` seems natural since we are not searching across a history of events but rather looking for individual vehicles that have specific properties.

Next came the event count filter. I tinkered with options like `count(>= N of ...)` or a dedicated block like `at least N of:`, but none of them really appealed to me. I settled on a new key phrase `{at most|exactly|at least} N occurrences of`. It's a bit lengthy but allows for very naturally worded expressions. The wording also disambiguates it from a phrase like `at least N of`, which I might use in other contexts (perhaps as a block) to filter on at least `N` conditions being true.

```sql
-- Reference query 2
vehicle has:
  make = "Toyota"
history contains:
  at least 3 occurrences of "diagnostic" / "P1155"
  no "o2 sensor replaced"
```

For the absence of an event, a plain `no` seemed the obvious choice.

### History Patterns

We come now to the third reference query, which presents a couple of interesting challenges. The first is how to express these [pattern overlays](#pattern-overlay) in an accessible manner. The visual abstraction is pretty intuitive; I needed to translate it into clear, structured text.

I confess that here I was very attracted to the idea of using something inspired by [Cypher][cypher] or GraphFrames's [motif finding syntax][motif]. We are not working with graphs, but a timeline of events is commonly represented as nodes on a line just as we visualized it earlier in this post. So my first cut at this problem took direct inspiration from that syntax.

[motif]: https://graphframes.io/04-user-guide/04-motif-finding.html

```sql
history contains:
  "transmission fluid change" -[<= 6 months]-> "transmission rebuild"
```

What I like about this is that it closely mirrors the pattern overlay visualization I sketched out earlier on in the design process. It's also quite concise and extends well to longer patterns like `A -[<= 6 months]-> B -[<= 1 year]-> C`. The main problem with it is the `-[<duration>]->` syntax, of course, which is quite unfriendly to non-programmers, especially compared to the rest of the language.

```sql
history contains:
  "transmission fluid change" then after at most 6 months "transmission rebuild"
```

I went with this more verbose English syntax.[^plural] It supports the common variations on history patterns my users care most about. The `then` makes the order of events explicit. A common variation users also asked for is one where the order _doesn't_ matter, just that the events are within a certain window of time of one another. A Cypher-inspired syntax would probably be something like `-[<duration>]-` (i.e. without the arrow heads), but in this case a simple `within <duration> of` is direct and clear.

[^plural]: With all this talk about English, I also took the time during implementation to enforce proper pluralization. e.g. `1 month` and not `1 months`.

```sql
-- history patterns
A then B
A then after {at most|exactly|at least} N {days|months|years} B
A then within N {days|months|years} B -- shorthand for `after at most`, which is very common
A within N {days|months|years} of B
```

An important note about the semantics of reusing an event: Each history pattern is independent. I think as a default this matches what most users would expect, but it has some important consequences.

```sql
-- matches BCAB
history contains:
  A then B
  B then C

-- does not match BCAB
history contains:
  A then B then C
```

One is that we have no way to express patterns that refer to the same occurrence of a specific event. It's a more advanced use case that we can leave to the future, should the need arise.

<!-- More generally: Precise semantics were hammered out with our small group of internal users during implementation. -->

### Unfinished Ideas

In the course of designing this new language I sketched out a lot of ideas that seemed relevant but didn't make the cut for the initial release. They are good candidates for subsequent releases of the language, with more refinement and validation from the user base.

<style>
  .unfinished-idea {
    margin: 0.5rem 0 1rem;
  }

  .unfinished-idea > summary {
    cursor: pointer;
    font-size: 1.05em;
    font-weight: 600;
    line-height: 1.4;
  }

  .unfinished-idea[open] > summary {
    margin-bottom: 0.4rem;
  }
</style>

<details markdown="1" class="unfinished-idea">
<summary>
Event Aliases
</summary>

Now that we have a syntax for history patterns, a new problem arises: How do users describe events that are more complex than can be supported by the `"value of work_done"` shorthand we've developed? We can't exactly fit in a full event definition as part of the history pattern unless we turn the pattern itself into a block or something like that.

I liked the idea of having event aliases, borrowing from SQL's Common Table Expression (CTE) syntax.

```sql
with events:
  tx_change:
    work_done = "transmission fluid change"
    detail = "AW-1"
    ... -- more conditions as necessary
  tx_rebuild:
    work_done = "transmission rebuild"
history contains:
    tx_change then after at most 1 year tx_rebuild
```

This lets users capture potentially lengthy event definitions in a handy name they can reference throughout the query.

</details>

<details markdown="1" class="unfinished-idea">
<summary>
Lookback Periods
</summary>

Something I found annoying when expressing queries in SQL was all the date arithmetic. It's common in many domains that analyze event data to limit searches to a specific window of time reaching back from the present, commonly known as a "lookback period". But SQL doesn't give you a convenient shorthand for this. Instead, you have to filter dates on some variation of `DATEDIFF(NOW(), num_days)`.

Users didn't complain specifically about this, but when I proposed a dedicated shorthand for expressing lookback periods they agreed it was handy.

```sql
with events:
  recent_tune_up:
    work_done = "tune up"
    -- as convenient comparison operator for attributes
    -- avoids need to use SQL functions like `NOW()` and `DATEDIFF()`
    time within past 1 year
history contains:
  recent_tune_up then after at most 6 months "diagnostic" / "P0524"
  -- as lookback filter for all patterns with a block
  within past 2 years:
    at least 2 occurrences of "oil change"
```

I found so many places this could be used to good effect, but I am a bit worried that I have overloaded the word `within` too much. Time will tell, I guess.

</details>

<details markdown="1" class="unfinished-idea">
<summary>
Reusing Query Results
</summary>

Analysts are almost certain to want to reuse population definitions. And while a custom execution environment built for our language can address this in part (e.g. letting you name and save queries in a database), direct language support also makes sense. It would enable users to break up large queries into smaller, meaningful parts, which could then be reused. The idea is a basic analog of database views.

Consider our first reference query. I am imagining being able to name the results of that query and then reuse them in another query.

```sql
population "rice-rockets":
  history contains:
    "intake upgrade"
    "exhaust upgrade"
    "fuel/air ratio reconfig"

population "rice-rockets-jp":
  vehicle has:
    membership in population "rice-rockets"
    any:
      make = "Toyota"
      make = "Honda"
      make = "Mazda"
```

The new `membership in` works but seems over-specialized. And we'll need some way to distinguish locally-scoped population definitions from global ones available to all users, and figure out how that would play with our execution environment.

</details>

<details markdown="1" class="unfinished-idea">
<summary>
Built-In Event Definitions
</summary>

I can imagine at least one built-in event that would be handy to have, and that would be an `anything` event that matches any event in the history. It would be used to test for recent activity and filter out vehicles that are likely inactive or otherwise off the grid.

```sql
history contains:
  within past 1 year:
    anything
```

We'd probably want to reserve the names of these built-in event definitions so they cannot be overridden by event aliases, or scope them differently somehow so it's clear they are globally defined events.

</details>

<details markdown="1" class="unfinished-idea">
<summary>
Cross-Pattern Event References
</summary>

Earlier we considered the problem of expressing patterns that refer to the same occurrence of an event. A good example is wanting to find histories where events `B` and `C` both occurred within six months after the same instance of event `A`.

```sql
history contains:
  A then within 6 months B
  A then within 6 months C
```

Right now, we can't express that. The above query will happily return a history like `AB <2-year gap> AC`, which isn't what we want. We need some way to indicate that event `A` needs to be the same instance of the event across the two patterns.

```sql
history contains:
  for the same occurrence of A:
    A then within 6 months B
    A then within 6 months C
```

This is the rough syntax I have in mind to solve this, though it's quite verbose. I considered alternatives like giving each event an optional suffix like `A:1` or `A#1` that can be used across patterns to indicate "the same occurrence of this event", but it seems too technical for our intended users.

</details>

<details markdown="1" class="unfinished-idea">
<summary>
Event Occurrence Selectors
</summary>

Sometimes analysts want to refer to a specific occurrence of an event in an event pattern, most commonly the first or last occurrence of an event. For example, say we want to match `A then B`, but only if `B` happened within `N` days after the first occurrence of `A`. This pattern wouldn't match a history that looked like `A <long gap> AB`.

Users can perhaps build this awkwardly with the current syntax:

```sql
history contains:
  no A then A then within N days B
```

But perhaps better would be if they could say something more direct:

```sql
history contains:
  first occurrence of A then within N days B
```

</details>

<details markdown="1" class="unfinished-idea">
<summary>
Selecting Matching Events
</summary>

Our analysts mainly need to know what vehicles match a given query, so right now running a query just returns a set of vehicles. The results are automatically visualized in our custom execution environment, with summary statistics about the vehicle population.

One thing analysts have asked about that the language doesn't do is show what events or event sequences matched the query. In other words, they sometimes want to know _why_ a vehicle was returned by a query without having to manually review the maintenance history themselves.

This may be more a problem to solve in the execution environment than in the language itself. I'm not sure. I almost certainly don't want to expand the language into reinventing SQL's `SELECT` clause or anything close to that.

</details>


## Design is a Non-Linear Process

I've walked you through my design process step by step, but the real design process was not this linear. Some ideas went through many iterations before I settled on something. Sometimes you run with an idea you think is solid until you get to the implementation and then realize there is something incoherent or incorrect about it; the realization takes you back to the beginning to rework the design. Sometimes you step away from the problem for days or weeks, during which ideas incubate subconsciously, and emerge evolved when you return to the problem.

As for the design of the language itself, you may find it good or bad. That's not so much the point of this post.[^point] Rather, what I wanted to share was the process. It was fun. It was intellectually satisfying. And it wasn't too difficult. If the big picture problem I described here reminds you of something you're facing, consider designing your own query language. Even if it doesn't see the light of day[^light], you will learn a lot in the process.

[^point]: That being said, if you've worked on a similar problem and would like to share your experience, please reach out! My contact information is in the site footer.
[^light]: My original work on this generated some excitement but was ultimately rejected by the business, which wasn't surprising. They hadn't asked for a new query language, and they had other fish to fry. Such is the fate of most work-related passion projects.

[In the next post][next] I will walk through how to implement a portion of this new query language so we have a working prototype!

[next]: {% post_url 2026-06-08-custom-query-language-implementation %}

_Thanks to Ciprian and [Eddie] for reading a draft of this post._

[Eddie]: https://github.com/erp12
