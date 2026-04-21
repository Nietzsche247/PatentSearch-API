-- eq
select patent_id, title as patent_title, date as patent_date from patents where patent_id = 11191203;

-- gte
select patent_id, title as patent_title, date as patent_date from patents where date >= "2023-09-01";

-- lte
select patent_id, title as patent_title, date as patent_date from patents where date <= "1976-01-31";

-- neq
select patent_id, title as patent_title, date as patent_date, type as patent_type from patents where type != "utility";

-- begins
select p.patent_id, title as patent_title, date as patent_date, p.type as patent_type, concat('[', group_concat(distinct '{"assignee_organization": "',pa.organization,'"}'), ']') as assignees from patents p inner join patent_assignee pa on p.patent_id = pa.patent_id where lower(organization) like "apple%" group by 1,2,3,4;

-- contains
select p.patent_id, title as patent_title, date as patent_date, p.type as patent_type, concat('[', group_concat(distinct '{""inventor_name_first"": ""',pi.name_first,'""}'), ']') as inventors from patents p inner join patent_inventor pi on p.patent_id = pi.patent_id where lower(name_first) like "%sarvo%" group by 1,2,3,4;

-- text_all
select patent_id, title as patent_title, date as patent_date, type as patent_type from patents where lower(title) REGEXP '\\btransfer\\b' and lower(title) REGEXP '\\bdata\\b' and lower(title) REGEXP '\\bdevice\\b';

-- text_any
select patent_id, title as patent_title, date as patent_date, type as patent_type from patents where lower(title) REGEXP '\\bmy\\b' or lower(title) REGEXP "\\btest(?!\\')\\b";

-- text_phrase
select patent_id, title as patent_title, date as patent_date, type as patent_type from patents where title REGEXP '\\bassembly.? comprising\\b';

-- and
select p.patent_id, title as patent_title, date as patent_date, p.type as patent_type, concat('[', group_concat(distinct '{"assignee_city": "',pa.city,'", "assignee_sequence": ',pa.sequence, '}'), ']') as assignees, concat('[', group_concat(distinct '{"inventor_city": "',pi.city,'", "inventor_sequence": ',pi.sequence, '}'), ']') as inventors from patents p inner join patent_assignee pa on p.patent_id = pa.patent_id inner join patent_inventor pi on p.patent_id = pi.patent_id inner join (select p.patent_id from patents p inner join patent_assignee pa on p.patent_id = pa.patent_id  inner join patent_inventor pi on p.patent_id = pi.patent_id where pi.city = 'Boston' and pa.city = 'Boston') p2 on p.patent_id = p2.patent_id group by 1,2,3,4;

-- or
select p.patent_id, title as patent_title, date as patent_date, p.type as patent_type, concat('[', group_concat(distinct '{"assignee_city": "',pa.city,'", "assignee_sequence": ',pa.sequence, '}'), ']') as assignees, concat('[', group_concat(distinct '{"inventor_city": "',pi.city,'", "inventor_sequence": ',pi.sequence, '}'), ']') as inventors from patents p left join patent_assignee pa on p.patent_id = pa.patent_id left join patent_inventor pi on p.patent_id = pi.patent_id inner join (select p.patent_id from patents p left join patent_assignee pa on p.patent_id = pa.patent_id  left join patent_inventor pi on p.patent_id = pi.patent_id where pi.city = 'Boston' or pa.city = 'Boston') p2 on p.patent_id = p2.patent_id group by 1,2,3,4;

-- value_list
select patent_id, title as patent_title, date as patent_date from patents where patent_id in (11191203, 11191204);

-- mix_text_and_non_text
select p.patent_id, title as patent_title, date as patent_date, p.type as patent_type, concat('[', group_concat(distinct '{"assignee_city": "',pa.city,'", "assignee_sequence": ',pa.sequence, '}'), ']') as assignees, concat('[', group_concat(distinct '{"inventor_city": "',pi.city,'", "inventor_sequence": ',pi.sequence, '}'), ']') as inventors from patents p left join patent_assignee pa on p.patent_id = pa.patent_id left join patent_inventor pi on p.patent_id = pi.patent_id where pi.city = 'Boston' and (lower(title) REGEXP '\\bassembly\\b' or lower(title) REGEXP '\\bcomponent\\b') group by 1,2,3,4;

-- keyword
select p.patent_id, title as patent_title, concat('[', group_concat(distinct '{"assignee_organization": "',pa.organization,'"}'), ']') as assignees from patents p left join patent_assignee pa on p.patent_id = pa.patent_id where pa.organization = 'CNH Industrial Canada, Ltd.' group by 1,2;

-- keyword_text_search
select p.patent_id, title as patent_title, concat('[', group_concat(distinct '{"assignee_organization": "',pa.organization,'"}'), ']') as assignees from patents p left join patent_assignee pa on p.patent_id = pa.patent_id where lower(pa.organization) REGEXP '\\bcnh\\b' group by 1,2;

-- contains_with_space
select p.patent_id, title as patent_title, concat('[', group_concat(distinct '{"assignee_organization": "',pa.organization,'"}'), ']') as assignees from patents p left join patent_assignee pa on p.patent_id = pa.patent_id where pa.organization like '%medical corp%' group by 1,2;

-- contains_list
select p.patent_id, title as patent_title, concat('[', group_concat(distinct '{"assignee_organization": "',pa.organization,'"}'), ']') as assignees from patents p left join patent_assignee pa on p.patent_id = pa.patent_id where lower(organization) REGEXP '\\bfruit\\b' or lower(organization) REGEXP '\\bvegetable\\b' group by 1,2;
