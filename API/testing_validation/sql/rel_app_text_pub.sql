-- eq
select document_number, text as related_text from rel_app_text where document_number = 20210378158 order by document_number;

-- contains
select document_number, text as related_text from rel_app_text where lower(text) REGEXP '\\bstatement\\b' and lower(text) REGEXP '\\bof\\b' and lower(text) REGEXP '\\brelated\\b' and lower(text) REGEXP '\\bcases\\b' order by document_number;
