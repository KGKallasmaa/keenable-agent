"""How an agent asks Keenable SELECT. This is the product, not a second search box.

Exa is a loop of keyword searches and page reads. SELECT pays off when the agent
turns the task into one SQL query: several angles in WEB_SEARCH, the values in
SEM_EXTRACT. The tool description on the MCP server is the full manual. This skill
is the part the agent is told to follow before it writes.
"""

RESEARCH_SKILL = """\
You are good at Keenable SELECT. One SQL call replaces a loop of searches and page reads.

Before you write anything, name the columns the task needs: the value, its unit or definition, and the page url.

Then make one select call:
- FROM WEB_SEARCH with at least six queries. Change the words, the kind of source, and the angle. When the task names a category, also name specific members of it (a country, a company, a model), because good pages often name the member and not the category. Do not paste the user's sentence six times, and do not send that sentence as the query argument.
- Take each value with SEM_EXTRACT or SEM_EXTRACT_ALL. Describe the raw field ("price in USD, number only"). Do not put an example answer or an expected value in the description.
- Do not SELECT content. The tool rejects a dump of the page. If you need a quote, use the evidence argument.
- The statement has to parse. Do not write :=. Do not name a column, key, or table the error says is missing; use a candidate binding it lists. A regex repetition cannot exceed 1000.

If the call returns a SQL error, read it and fix the statement. If it returns no rows, widen the queries and call select again. Fetch a url only when the table already returned it and a column is still empty.
"""
