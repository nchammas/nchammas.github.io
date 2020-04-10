---
layout: post
title: "Postgres and Redshift: Why use both?"
permalink: /writing/:title
tags: [databases]
---

A couple of co-workers who are new to database technology recently asked me why we use both Postgres and Redshift in our stack. They're both SQL databases and seem to do the same thing. So why not just use one technology? It would be simpler.

It's a great question. In fact, it's very common for teams to use a combination of databases in their stack, especially a combination like Postgres and Redshift. Let's explore why.

## Understanding Database Types via their Access Patterns

There are a lot of database types in the world. A very powerful way to understand any given database is via the _access patterns_ it is designed for.

At a high level, we can divide databases into two broad categories:

* transactional
* analytical

(Note: We're not talking about transactions in the sense of [ACID]; we're focusing just on the transactional access pattern.)

[ACID]: https://en.wikipedia.org/w/index.php?title=ACID_(computer_science)

## Transactional Databases

A transactional database is what most people think of when they hear "database". It's typically a database that backs online, interactive operations, like for a store or a game, where users expect instant responses to their queries.

Each query typically touches a very small amount of data, since the user executing the query is usually only reading or writing data about themselves, like updating their profile information or noting a new purchase. Tables tend to be narrow and highly [normalized](https://docs.microsoft.com/en-us/office/troubleshoot/access/database-normalization-description). At any one time there may be thousands or tens of thousands of such queries executing against a transactional database, as a multitude of people interact with the service that the database backs.

Query times in well-operated transactional databases are typically measured in milliseconds or less. Engineers spend a lot of time designing table indexes to enable the database to sift through the minimum number of rows required to answer a query, and tuning database parameters to keep as much data in memory as possible and minimize disk I/O.

In summary, transactional databases are designed for:
* point reads/writes, i.e. small amounts of data per query
* high concurrency, i.e. many queries running at the same time
* very low latency, i.e. quick query response times

Examples of transactional databases include all the popular database systems you've heard of:
* MySQL
* Oracle
* Postgres
* SQL Server

<div style="text-align: center;">
<figure>
    <a href="http://www.warfaremagazine.co.uk/articles/1415-The-Battle-of-Agincourt/171">
        <img
            src="/assets/images/battle-of-agincourt-compressed.jpg"
            width="300"
        />
    </a>
    <figcaption>
        The transactional database access pattern: Lots and lots of tiny chunks of data coming at you real fast.
    </figcaption>
</figure>
</div>

## Analytical Databases

An analytical database is designed for a very different access pattern. Instead of backing an online store or game, an analytics database typically backs pipelines or tools that help users analyze large swathes of data.

A typical analytics query will touch a large range of data, like a reporting query that summarizes sales numbers by day for an entire quarter. Compared to a transactional database, there will only be a small number of queries running at one time against an analytics database, and each query will usually only touch a handful of columns in any given table. Tables tend to be wide (i.e. they have many columns) and highly denormalized.

Query times in an analytics database will typically be on the order of seconds or minutes. Indexes, which help queries quickly find their target row, aren't relevant to analytics databases because queries rarely target a single row. Instead, engineers optimize analytical databases by reorganizing how the data is stored on disk to minimize the number of _columns_ that need to be read, and by compressing the data so that large chunks of data can be read quickly. Trying to hold most of your data in memory is typically not possible or even necessary for analytics workloads.

In summary, analytical databases are designed for:
* bulk reads/writes, i.e. large amounts of data per query
* lower concurrency, i.e. fewer queries running at the same time
* higher latency, i.e. longer query response times

Popular analytical database systems include:
* Vertica
* Redshift
* Teradata

<div style="text-align: center;">
<figure>
    <a href="https://en.wikipedia.org/wiki/Trebuchet">
        <img
            src="/assets/images/trebuchet-castelnaud-compressed.jpg"
            width="400"
        />
    </a>
    <figcaption>
        The analytical database access pattern: A handful of huge chunks of data coming at you relatively slowly.
    </figcaption>
</figure>
</div>

## Typical Usage Examples

Now we can answer the question that opened this post: Why use both Postgres and Redshift?

A typical pattern is for teams to use both to build an analytics product. For example, consider a team building a product that tracks visits to your website and then shows you a handy chart summarizing your web traffic over the past few weeks.

The team uses Redshift to bulk load detailed event data tracking every visit to your site and then aggregate it down to a set of summary statistics and key metrics. They then load that summarized data -- for example, total visits to your site per day for the past 12 weeks -- into Postgres and serve it up from there to a website or API endpoint for users to access. Redshift answers a relatively small number of queries that crunch a lot of data and take a lot of time each, as part of a batch update pipeline, while Postgres answers a larger number of lighter queries that touch smaller amounts of data each, as users browse the summary statistics for their website.

It's also common for the flow between the systems to go the other way around. Consider a team building an online game--an [MMORPG], perhaps.

They use Postgres to back online game operations and track what actions a player is taking in the game. Those actions affect the online world and develop the player's character as they are playing the game. The game only needs to know what a player has done in the current session, so to keep the transactional database light, the team regularly moves data for old sessions from Postgres to Redshift. In Redshift, analysts study player behavior across long stretches of time and try to answer questions like "What is the most popular path players take through our world?" or "Where are players quitting our game, and why?" Postgres handles the flurry of detail-level activity to serve thousands of online players, while Redshift answers big picture queries for a handful of in-house analysts.

[MMORPG]: https://en.wikipedia.org/wiki/Massively_multiplayer_online_role-playing_game

## Final Thoughts

There are many more ways to understand and categorize database systems:
* by the [consistency guarantees] they provide;
* by the levels of [transaction isolation] they provide;
* by how they [scale] to handle additional load;
* by the [query languages] and [data structures] they support;
* or by how they [lay out data on disk], to name a few.

[consistency guarantees]: https://fauna.com/blog/demystifying-database-systems-introduction-to-consistency-levels
[transaction isolation]: http://martin.kleppmann.com/2014/11/25/hermitage-testing-the-i-in-acid.html
[scale]: https://docs.microsoft.com/en-us/azure/sql-database/sql-database-elastic-scale-introduction#horizontal-and-vertical-scaling
[query languages]: https://neo4j.com/blog/why-database-query-language-matters/#cypher
[data structures]: https://www.mongodb.com/document-databases
[lay out data on disk]: https://en.wikipedia.org/wiki/Column-oriented_DBMS

In this post we've focused just on access patterns, though databases designed for different access patterns typically do so by differing on these other axes, too.

What about systems like Amazon Athena and Spark SQL, by the way? Many teams with data-intensive workflows tend to use these tools as well. And they certainly _look_ like databases, though there's something weird about them. Roughly speaking, systems like Athena and Spark SQL _can_ be categorized as analytical databases, but there's more to them than that. We'll explore these systems in more detail in a follow-up post.

_Thanks to Michelle, Yuna, Sam, Cip, Fabian, and Roland for reading drafts of this post._
