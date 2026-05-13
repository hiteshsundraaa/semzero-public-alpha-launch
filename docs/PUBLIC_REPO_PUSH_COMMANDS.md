# Public Repo Push Commands

Use these after creating an empty GitHub repository at `hiteshsundraaa/semzero`.

```bash
git init
git add .
git commit -m "Initial public alpha release"
git branch -M main
git remote add origin https://github.com/hiteshsundraaa/semzero.git
git push -u origin main

git tag v0.8.0-alpha.1
git push origin v0.8.0-alpha.1
```

Do not commit generated caches, `dist/`, local SQLite databases, or historical zip artifacts.
