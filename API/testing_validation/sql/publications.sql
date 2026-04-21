-- eq
select document_number, title as publication_title, date as publication_date from publication where document_number = 20120017343 order by document_number;

-- gte
select document_number, title as publication_title, date as publication_date from publication where date >= "2023-09-15" order by document_number;

-- lte
select document_number, title as publication_title, date as publication_date from publication where date <= "2001-03-31" order by document_number;

-- neq
select document_number, title as publication_title, date as publication_date, type as publication_type from publication where type != "utility" order by document_number;

-- begins
select p.document_number, title as publication_title, date as publication_date, p.type as publication_type, concat('[', group_concat(distinct '{"assignee_organization": "',pa.organization,'"}'), ']') as assignees from publication p inner join publication_assignee pa on p.document_number = pa.document_number where lower(organization) like "apple%" group by 1,2,3,4;

-- contains
select p.document_number, title as publication_title, date as publication_date, p.type as publication_type, concat('[', group_concat(distinct '{""inventor_name_first"": ""',pi.name_first,'""}'), ']') as inventors from publication p inner join publication_inventor pi on p.document_number = pi.document_number where lower(name_first) like "%sarvo%" group by 1,2,3,4;

-- text_all
select document_number, title as publication_title, date as publication_date, type as publication_type from publication where lower(title) REGEXP '\\btransfer\\b' and lower(title) REGEXP '\\bdata\\b' and lower(title) REGEXP '\\bdevice\\b' order by document_number;

-- text_any
select document_number, title as publication_title, date as publication_date, type as publication_type from publication where lower(title) REGEXP '\\bmy\\b' or lower(title) REGEXP "\\btest(?!\\')\\b" order by document_number;

-- text_phrase
select document_number, title as publication_title, date as publication_date, type as publication_type from publication where title REGEXP '\\bassembly.? comprising\\b' order by document_number;

-- and
select p.document_number, title as publication_title, date as publication_date, p.type as publication_type, concat('[', group_concat(distinct '{"assignee_city": "',pa.city,'", "assignee_sequence": ',pa.sequence, '}'), ']') as assignees, concat('[', group_concat(distinct '{"inventor_city": "',pi.city,'", "inventor_sequence": ',pi.sequence, '}'), ']') as inventors from publication p left join publication_assignee pa on p.document_number = pa.document_number left join publication_inventor pi on p.document_number = pi.document_number inner join (select p.document_number from publication p left join publication_assignee pa on p.document_number = pa.document_number  left join publication_inventor pi on p.document_number = pi.document_number where pi.city = 'Boston' and pa.city = 'Boston') p2 on p.document_number = p2.document_number group by 1,2,3,4 order by document_number;

-- or
select p.document_number, title as publication_title, date as publication_date, p.type as publication_type, concat('[', group_concat(distinct '{"assignee_city": "',pa.city,'", "assignee_sequence": ',pa.sequence, '}'), ']') as assignees, concat('[', group_concat(distinct '{"inventor_city": "',pi.city,'", "inventor_sequence": ',pi.sequence, '}'), ']') as inventors from publication p left join publication_assignee pa on p.document_number = pa.document_number left join publication_inventor pi on p.document_number = pi.document_number inner join (select p.document_number from publication p left join publication_assignee pa on p.document_number = pa.document_number  left join publication_inventor pi on p.document_number = pi.document_number where pi.city = 'Boston' or pa.city = 'Boston') p2 on p.document_number = p2.document_number group by 1,2,3,4 order by document_number;

-- value_list
select document_number, title as publication_title, date as publication_date from publication where document_number in (20120017343, 20120017344) order by document_number;

-- mix_text_and_non_text
select p.document_number, title as publication_title, date as publication_date, p.type as publication_type, concat('[', group_concat(distinct '{"assignee_city": "',pa.city,'", "assignee_sequence": ',pa.sequence, '}'), ']') as assignees, concat('[', group_concat(distinct '{"inventor_city": "',pi.city,'", "inventor_sequence": ',pi.sequence, '}'), ']') as inventors from publication p left join publication_assignee pa on p.document_number = pa.document_number left join publication_inventor pi on p.document_number = pi.document_number where pi.city = 'Boston' and (lower(title) REGEXP '\\bassembly\\b' or lower(title) REGEXP '\\bcomponent\\b') group by 1,2,3,4 order by document_number;

-- keyword
select p.document_number, title as publication_title, concat('[', group_concat(distinct '{"assignee_organization": "',pa.organization,'"}'), ']') as assignees from publication p left join publication_assignee pa on p.document_number = pa.document_number where pa.organization = 'CNH Industrial Canada, Ltd.' group by 1,2 order by document_number;

-- keyword_text_search
select p.document_number, title as publication_title, concat('[', group_concat(distinct '{"assignee_organization": "',pa.organization,'"}'), ']') as assignees from publication p left join publication_assignee pa on p.document_number = pa.document_number where lower(pa.organization) REGEXP '\\bcnh\\b' group by 1,2 order by document_number;
