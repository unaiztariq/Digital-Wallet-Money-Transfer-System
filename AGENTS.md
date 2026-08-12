1. Understand the requirement first - list concrete features in priority
   order; ask ONE clarifying question if vague; note what's out of scope.
2. Scaffold with the framework's own tools (django-admin startproject,
   create-next-app, npm init); install only what's needed.
3. Build the simplest thing that satisfies the requirement. No
   over-engineering, no speculative columns/classes/abstractions.
4. Admin stays minimal: list_display only (+ search/filter/actions only when
   clearly useful). No custom manage.py commands - seed via fixtures/scripts/
   test setup.
5. Use the framework's built-ins (forms, generic views, fixtures) instead of
   reimplementing.
6. Test only the core flows; run existing lint/checks; verify end-to-end.
7. Secrets live in env files and are never logged, committed, or shared.
