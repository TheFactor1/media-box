# AIOStreams v2 — user-config API for "paste your debrid key" onboarding

Researched 2026-10-27 against `main` of https://github.com/Viren070/AIOStreams (latest release v2.37.0, published 2026-10-23, https://api.github.com/repos/Viren070/AIOStreams/releases/latest). "Source" = file on `main` as of today; "docs" = https://docs.aiostreams.viren070.me. Items marked **unverified** were not confirmed in source.

## 1. How configs are stored and addressed

- Configs live in the instance DB (SQLite or Postgres, `DATABASE_URI`), keyed by a server-generated UUID; the password is stored hashed and also returned encrypted (`encryptedPassword`) for use in URLs. Source: `packages/core/src/db/repositories/users.ts` (`createUser` -> `generateUUID()`, `getTextHash(password)`, `encryptString(password)`).
- **Manifest URL shape:** `BASE_URL/stremio/<uuid>/<encryptedPassword>/manifest.json`. Source: `packages/server/src/app.ts` mounts `app.use('/stremio/:uuid/:encryptedPassword', stremioAuthRouter)` and `stremioAuthRouter.use('/manifest.json', manifest)`. Alias form `/stremio/u/<alias>/manifest.json` exists via `ALIASED_CONFIGURATIONS` (docs /configuration/environment-variables/).
- **HTTP API exists:** base `/api/v1` (`app.use(\`/api/v${constants.API_VERSION}\`)`; docs /apis/ state `/api/v1`). User routes in `packages/server/src/routes/api/user.ts`:
  - `HEAD /api/v1/user?uuid=` — exists check, no auth.
  - `POST /api/v1/user` — **create, no auth.** Body: `{ "config": <UserData>, "password": "<min 6 chars>" }` (`const { config, password } = req.body`; missing -> `MISSING_REQUIRED_FIELDS`). Returns 201 `{ success, detail, error, data: { uuid, encryptedPassword } }`. Rate-limited (`userCreateRateLimiter`).
  - `GET /api/v1/user[?raw=true]` — Basic auth `uuid:password` (raw password or `encryptedPassword` both accepted). Returns `data.userData`, `data.encryptedPassword`.
  - `PUT /api/v1/user` — Basic auth, body `{ "config": <full UserData> }`, full replacement (no partial update). Docs /apis/user/.
  - `DELETE /api/v1/user` — Basic auth, irreversible.
  - Also `POST /api/v1/user/password`, `POST /api/v1/user/verify` (source `user.ts`).
- **No `ADDON_PASSWORD`/`API_KEY` env is needed.** Neither name appears in `.env.sample` or the env-var docs. The only create gate is `CONFIG_ACCESS_KEY`: when set (or `AIOSTREAMS_AUTH_REQUIRED=true`, which "enforces CONFIG_ACCESS_KEY"), POST requires an operator session cookie `aiostreams.session` whose user has the `createConfig` permission (`AIOSTREAMS_AUTH_PERMISSIONS`). Source: `user.ts` (`getConfigAccessKey()` + `sessionHasPermission(req.user, Permission.CreateConfig)`), `packages/server/src/middlewares/auth.ts` (`attachSession` reads cookie only), docs env page. For our flow: leave `CONFIG_ACCESS_KEY` unset and anonymous POST works.
- Response envelope for all `/api/v1` calls: `{ success, detail, error: {code, message} | null, data }` (docs /apis/).

## 2. Minimal valid config

Schema: `UserDataSchema` in `packages/core/src/db/schemas.ts`. Required top-level keys: `formatter`, `presets`, `sortCriteria`. Everything else optional (`services`, `tmdbApiKey`, `uuid`, `accessKey`, ...).

```jsonc
{
  "config": {
    "services": [ { "id": "torbox", "enabled": true, "credentials": { "apiKey": "<KEY>" } } ],
    "presets": [
      { "type": "torrentio", "instanceId": "80c", "enabled": true,
        "options": { "name": "Torrentio", "timeout": 5000, "resources": ["stream"], "providers": [], "useMultipleInstances": false } }
    ],
    "formatter": { "id": "prism" },
    "sortCriteria": { "global": [ { "key": "cached", "direction": "desc" }, { "key": "resolution", "direction": "desc" } ] }
  },
  "password": "<>=6 chars>"
}
```

- `ServiceSchema = { id: z.enum(constants.SERVICES), enabled?: boolean, credentials: Record<string,string> }` (schemas.ts). Service ids and credential key, all `apiKey`: `realdebrid`, `alldebrid`, `premiumize`, `torbox` (`packages/core/src/utils/constants.ts`, `SERVICE_DETAILS`). Credential check is format-only; no call to the debrid API at create time (`validateConfig` in `packages/core/src/utils/config.ts`).
- `PresetSchema = { type, instanceId, enabled, options: Record<string,any>, category? }`; `instanceId` must be unique and contain no dot (config.ts).
- `formatter.id` from `constants.FORMATTERS` ("prism" used by the official template); `sortCriteria.global` is the only required sort array, keys seen in the official template: cached, resolution, library, regexPatterns, streamType, visualTag, audioTag, audioChannel, encode, language, size, direction `desc`.
- **Does AIOStreams need upstream addons picked?** Yes: streams only come from `presets`; `validateConfig` imposes no minimum, so an empty list is "valid" but returns nothing. `createUser` calls `validateConfig(..., { skipErrorsFromAddonsOrProxies: false, increasedManifestTimeout: true, bypassManifestCache: true })`, so creation **likely fails if any enabled preset's upstream manifest is unreachable** (unverified: `AIOStreams.initialise()` body not read).
- Preset ids (file `packages/core/src/presets/<name>.ts`, `ID` field): `torrentio`, `comet`, `mediafusion`, `stremthruTorz`, `stremthruStore`, `library`, `knaben`, `newznab`, `usenet-streamer`, `torbox` (DISABLED, "TorBox addon is deprecated"), `torbox-search` (DISABLED/removed, "TorBox Search API ... has been shut down" — docs say removed in v2.32). Others exist (zilean, jackett, prowlarr, torznab, nzbhydra, debridio, easynews*, ...); full list is the 91 files in `packages/core/src/presets/`. `validatePreset` rejects any preset marked disabled, so **do not** send `torbox`/`torbox-search`.
- **TorBox usenet:** TorBox is a *playback service* for usenet (needs TorBox Pro + API key), not a search source. Usenet results must come from a Newznab-type preset (`newznab`, required options `name`, `api.url`; `apiKey` optional), NZBHydra2, Prowlarr, or the built-in engine (needs `AIOSTREAMS_AUTH` user with `service` permission). Docs /guides/usenet/; `newznab.ts` lists `TORBOX_SERVICE` in `SUPPORTED_SERVICES`. There is no key-only "TorBox native usenet" preset today.

## 3. Password, updates, export/import

- Password set at create (min 6, `USER_NEW_PASSWORD_TOO_SHORT`), changeable via `POST /api/v1/user/password`; full-config replace via `PUT /api/v1/user` with Basic auth (`updateUser` re-validates with `validateConfig`). Source: users.ts, user.ts.
- Export: UI "Backups > Export" downloads config JSON (docs /configuration/setup/). Import: no JSON-import API documented; `GET /api/v1/user` returns `userData`, which is the same object shape POST accepts, so **template = GET from a hand-built config, strip `uuid`/`encryptedPassword`, swap `services[].credentials.apiKey`, POST**.
- Official starter template: `resources/templates/debrid-starter.json` (`metadata.id` "builtin.debrid-starter", v1.0.1, `serviceRequired: true`, services list includes realdebrid/torbox/premiumize/alldebrid). Its `config` has presets torrentio, comet, stremthruTorz, animetosho, knaben, mediafusion plus full filter/sort blocks and `"tmdbApiKey": "<template_placeholder>"` (drop or fill; field optional). Templates (`{metadata, config}`) are a UI import flow (`/stremio/configure?template=<url>`, `TEMPLATE_URLS`, `/app/data/templates`); `GET /api/v1/templates` only lists them, no API "apply" (docs /reference/templates/, `routes/api/templates.ts`).

## 4. Docker / env for a self-hosted instance

- Image: `ghcr.io/viren070/aiostreams:latest` (`compose.yaml`); Docker Hub `viren070/aiostreams` also published (README). Pin `:v2.37.0` for reproducibility (tag naming unverified).
- compose.yaml: port `3000:3000`, `env_file: .env`, volume `./data:/app/data`.
- Required (`.env.sample`, docs env page): `SECRET_KEY` (64-hex, `openssl rand -hex 32`, cannot change later), `BASE_URL` (public URL, used to build install URLs), `DATABASE_URI` (default `sqlite://./data/db.sqlite`). Optional: `PORT`, `REDIS_URI`, `TMDB_API_KEY`, `AIOSTREAMS_AUTH`.
- To keep anonymous `POST /api/v1/user` open: do **not** set `CONFIG_ACCESS_KEY` or `AIOSTREAMS_AUTH_REQUIRED=true`. Lock down publicly if needed via `DISABLE_RATE_LIMITS=false` (default) and a reverse proxy; `TRUSTED_IPS` for forwarded headers.
- Useful: `DEFAULT_SERVICE_CREDENTIALS` / `FORCED_SERVICE_CREDENTIALS` (`serviceId.credentialId=value`) pre-fill or force creds instance-wide — not per-user, so not a substitute for the POST body.

## 5. Legal posture: aggregators vs scrapers (by what each preset queries)

Pure "your own debrid account" (no public index search):
- `library` — "Browse and stream from your service library" (library.ts).
- `stremthruStore` — "Access your debrid library through catalogs and streams" (stremthruStore.ts).

Public torrent index / DHT / hashlist scrapers (debrid key only used to resolve):
- `torrentio` — "torrent streams from a multitude of providers" (torrentio.ts); `comet` — "Torrent/Debrid addon" (comet.ts); `mediafusion` (mediafusion.ts); `stremthruTorz` — "crowdsourced torrent library supplemented by DMM hashlists" (stremthruTorz.ts); `knaben` — "indexer proxy ... including The Pirate Bay" (knaben.ts); also zilean, animetosho, torrentGalaxy, eztv, thePirateBay, bitmagnet, jackett/prowlarr/torznab (README built-in table). README: built-in torrent addons "need a debrid service to work".

Usenet (user-supplied indexer, debrid/NNTP for playback): `newznab`, `nzbhydra`, `prowlarr`, `usenet-streamer` (needs NZBDav/Stremio NNTP), built-in engine (docs /guides/usenet/, which also notes some indexers forbid streaming).

Recommendation: default onboarding config = `services` + `library` (+ `stremthruStore`) only; expose "add scrapers" as a link to `BASE_URL/stremio/<uuid>/<encryptedPassword>/configure` so the user opts in inside AIOStreams's own UI. **Unverified:** whether `library` alone yields playable streams for arbitrary titles — by design it only shows what is already in the account, so a key-only config will be empty until the user adds a search preset.

## Open questions

1. Does `createUser` fail hard when an upstream manifest (e.g. Torrentio) times out? Test on the v2.37.0 instance with a deliberately bad preset URL.
2. Exact `constants.FORMATTERS` / `SORT_CRITERIA` enums — read `constants.ts` or just reuse the debrid-starter template blocks verbatim.
3. Does `GET /api/v1/user` return `services[].credentials` decrypted (needed if we clone a template config)? `raw=true` exists; decryption behavior unverified.
