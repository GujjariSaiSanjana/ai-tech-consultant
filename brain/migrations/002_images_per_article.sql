-- The same Freshdesk attachment can appear in several articles (61 screenshots in the live help
-- center). Key images by (article, attachment) so each article owns its own row and removing one
-- article never strips the screenshot from another.
ALTER TABLE chunks DROP CONSTRAINT chunks_image_id_fkey;
ALTER TABLE images DROP CONSTRAINT images_pkey;
ALTER TABLE images ADD PRIMARY KEY (article_id, id);
DROP INDEX images_article;

-- Give every article that references a shared attachment its own row (the next re-index of that
-- article refreshes caption and position).
INSERT INTO images (id, article_id, url, change_key, position, caption, description, described_by)
SELECT DISTINCT ON (c.article_id, c.image_id)
       c.image_id, c.article_id, i.url, i.change_key, i.position, i.caption, i.description, i.described_by
FROM chunks c
JOIN images i ON i.id = c.image_id
WHERE c.image_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM images own WHERE own.article_id = c.article_id AND own.id = c.image_id)
ORDER BY c.article_id, c.image_id;

ALTER TABLE chunks ADD CONSTRAINT chunks_image_fkey
    FOREIGN KEY (article_id, image_id) REFERENCES images (article_id, id) ON DELETE SET NULL (image_id);
