---
target: HEAD~1..HEAD
---
This is going out behind a feature flag on Friday, so prefer a reversible change
over the right one. Anything that would need a migration to undo, say so plainly
and I will hold it back.

Do not relitigate the storage engine — that was decided last quarter and is not
in scope for this review.
