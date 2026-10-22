-- Two identical questions arriving together must not open two expert tickets.
DELETE FROM gap_tickets g USING gap_tickets keep
WHERE g.status = 'open' AND keep.status = 'open'
  AND lower(trim(g.question)) = lower(trim(keep.question))
  AND (g.created_at, g.id) > (keep.created_at, keep.id);
CREATE UNIQUE INDEX gap_tickets_one_open_per_question
    ON gap_tickets (lower(trim(question))) WHERE status = 'open';
