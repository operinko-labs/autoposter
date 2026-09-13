# Row 267: Jellyfin 12 OpenAPI capture

Fetched from:

- `https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json`
  sha256 `86ef6b6cea7e474b5bb234f44e5eb7f6e8f458ce6d7d84fab99e8d1f4a75ce39`

The spec reports `openapi: "3.0.4"`, `info.version: "12.0.0"`. The operator's
live instance is also 12.0.0 (verified live 2026-09-13, see below). Re-fetch
and re-hash before trusting this document against a different server version.

Every extract below was pulled from the file at that hash via the extraction
script quoted at the end of this document, not retyped by hand.

## Verified live, 2026-09-13

Against the operator's own Jellyfin 12.0.0 instance:

1. `Authorization: MediaBrowser Token="<key>"` on its own → `200` on
   `GET /System/Info`. Also `200` with `Client`, `Device`, `DeviceId` and
   `Version` fields added to the same header. Neither is required for this
   probe; both forms work.
2. The legacy `X-Emby-Token` header → `401` on 12.0. Do not use it.
3. `GET /Items` has **no** `anyProviderIdEquals` parameter (confirmed again
   in the spec extract below). `searchTerm`, `parentId`, `recursive`,
   `includeItemTypes`, `fields` and `years` all exist and were exercised.
4. `ProviderIds` in live responses use capitalised keys: `Tmdb`, `Imdb`,
   `Tvdb`, `TmdbCollection`. The spec's schema (below) types `ProviderIds` as
   a plain `additionalProperties` string dictionary — it does not enumerate
   key names, so this casing is a live-server fact, not a spec fact.
5. `POST /Items/{itemId}/Images/{imageType}` (`SetItemImage`) → `500` when
   the body is the raw image bytes the spec's `requestBody` schema below
   describes. The body must be **base64-encoded** instead, `Content-Type`
   unchanged (the real image mime, not `text/plain` or similar) — this is an
   observation about the live server, not something the spec says anywhere;
   `JellyfinApi.set_image` encodes accordingly (V3, §11).

The header **value** format (`MediaBrowser Token="<key>"`, with or without
the `Client`/`Device`/`DeviceId`/`Version` fields) is not written anywhere in
the spec — `securitySchemes.CustomAuthentication` below only says the header
is named `Authorization`. It comes from the project's own design doc; call
it V1 here since the spec gives no name for it.

## What the code depends on (verified below, verbatim)

- `GET /Items` takes `parentId`, `recursive`, `includeItemTypes`, `fields`,
  `searchTerm`, `years`, `ids` and 79 other query parameters, but not
  `anyProviderIdEquals` — there is no provider-id filter on this endpoint at
  all. The closest things to one are three booleans that only say whether an
  id of a given kind is present, not what it equals: `hasImdbId`,
  `hasTmdbId`, `hasTvdbId`.
- `POST /Items/{itemId}` takes a full `BaseItemDto` (155 properties). The 16
  fields the client writes are quoted individually below, matched against the
  real schema so a rename or type change on Jellyfin's side is caught here
  first.
- `ProviderIds` is untyped on the wire — `additionalProperties: {"type":
  "string", "nullable": true}` — so the capitalised key names (`Tmdb`,
  `Imdb`, `Tvdb`, `TmdbCollection`) are a live-server convention, not
  something the spec enforces.
- `securitySchemes` declares exactly one scheme, `CustomAuthentication`, an
  `apiKey` in the `Authorization` header. No OAuth2/bearer scheme exists.

---

## jellyfin-openapi-12.json → paths → "/System/Info" → get

```json
{
 "tags": ["System"],
 "summary": "Gets information about the server.",
 "operationId": "GetSystemInfo",
 "security": [{"CustomAuthentication": ["FirstTimeSetupOrIgnoreParentalControl", "DefaultAuthorization"]}]
}
```

No parameters. `200` response schema: `SystemInfo`, property names (27):
`LocalAddress, ServerName, Version, ProductName, OperatingSystem, Id,
StartupWizardCompleted, OperatingSystemDisplayName, PackageName,
HasPendingRestart, IsShuttingDown, SupportsLibraryMonitor,
WebSocketPortNumber, CompletedInstallations, CanSelfRestart,
CanLaunchWebBrowser, ProgramDataPath, WebPath, ItemsByNamePath, CachePath,
LogPath, InternalMetadataPath, TranscodingTempPath,
CastReceiverApplications, HasUpdateAvailable, EncoderLocation,
SystemArchitecture`. The client reads `Version`.

## jellyfin-openapi-12.json → paths → "/Library/VirtualFolders" → get

```json
{
 "tags": ["LibraryStructure"],
 "summary": "Gets all virtual folders.",
 "operationId": "GetVirtualFolders",
 "security": [{"CustomAuthentication": ["FirstTimeSetupOrElevated", "DefaultAuthorization"]}]
}
```

No parameters. `200` response: `array` of `VirtualFolderInfo`, verbatim:

```json
{
 "type": "object",
 "properties": {
  "Name": {"type": "string", "description": "Gets or sets the name.", "nullable": true},
  "Locations": {"type": "array", "items": {"type": "string"}, "description": "Gets or sets the locations.", "nullable": true},
  "CollectionType": {
   "enum": ["movies", "tvshows", "music", "musicvideos", "homevideos", "boxsets", "books", "mixed"],
   "allOf": [{"$ref": "#/components/schemas/CollectionTypeOptions"}],
   "description": "Gets or sets the type of the collection.",
   "nullable": true
  },
  "LibraryOptions": {"allOf": [{"$ref": "#/components/schemas/LibraryOptions"}], "nullable": true},
  "ItemId": {"type": "string", "description": "Gets or sets the item identifier.", "nullable": true},
  "PrimaryImageItemId": {"type": "string", "description": "Gets or sets the primary image item identifier.", "nullable": true},
  "RefreshProgress": {"type": "number", "format": "double", "nullable": true},
  "RefreshStatus": {"type": "string", "nullable": true}
 },
 "additionalProperties": false,
 "description": "Used to hold information about a user's list of configured virtual folders."
}
```

`Name`, `CollectionType`, `Locations` and `ItemId` are the fields the client
reads. `CollectionType` is a lowercase enum (`movies`, `tvshows`, ...), not
`PascalCase` like most other Jellyfin enums.

## jellyfin-openapi-12.json → paths → "/Items" → get

```json
{
 "tags": ["Library"],
 "summary": "Gets items based on a query.",
 "operationId": "GetItems",
 "security": [{"CustomAuthentication": ["DefaultAuthorization"]}]
}
```

86 query parameters total. **`anyProviderIdEquals` is absent** — confirmed
by listing every parameter name in the extraction script's output; there is
no parameter of that name anywhere on this operation. The ones the client
uses, verbatim:

```json
[
 {"name": "parentId", "in": "query", "description": "Specify this to localize the search to a specific item or folder. Omit to use the root.", "schema": {"type": "string", "format": "uuid"}},
 {"name": "recursive", "in": "query", "description": "When searching within folders, this determines whether or not the search will be recursive. true/false.", "schema": {"type": "boolean"}},
 {"name": "includeItemTypes", "in": "query", "description": "Optional. If specified, results will be filtered based on the item type. This allows multiple, comma delimited.", "schema": {"type": "array", "items": {"$ref": "#/components/schemas/BaseItemKind"}}},
 {"name": "fields", "in": "query", "description": "Optional. Specify additional fields of information to return in the output. This allows multiple, comma delimited. Options: Budget, Chapters, DateCreated, Genres, HomePageUrl, IndexOptions, MediaStreams, Overview, ParentId, Path, People, ProviderIds, PrimaryImageAspectRatio, Revenue, SortName, Studios, Taglines.", "schema": {"type": "array", "items": {"$ref": "#/components/schemas/ItemFields"}}},
 {"name": "searchTerm", "in": "query", "description": "Optional. Filter based on a search term.", "schema": {"type": "string"}},
 {"name": "years", "in": "query", "description": "Optional. If specified, results will be filtered based on production year. This allows multiple, comma delimited.", "schema": {"type": "array", "items": {"type": "integer", "format": "int32"}}},
 {"name": "ids", "in": "query", "description": "Optional. If specific items are needed, specify a list of item id's to retrieve. This allows multiple, comma delimited.", "schema": {"type": "array", "items": {"type": "string", "format": "uuid"}}}
]
```

The three `has*Id` booleans, verbatim (the closest thing to a provider-id
filter this operation has, and not one):

```json
[
 {"name": "hasImdbId", "in": "query", "description": "Optional filter by items that have an IMDb id or not.", "schema": {"type": "boolean"}},
 {"name": "hasTmdbId", "in": "query", "description": "Optional filter by items that have a TMDb id or not.", "schema": {"type": "boolean"}},
 {"name": "hasTvdbId", "in": "query", "description": "Optional filter by items that have a TVDb id or not.", "schema": {"type": "boolean"}}
]
```

`200` response schema: `BaseItemDtoQueryResult`, verbatim:

```json
{
 "type": "object",
 "properties": {
  "Items": {"type": "array", "items": {"$ref": "#/components/schemas/BaseItemDto"}, "description": "Gets or sets the items."},
  "TotalRecordCount": {"type": "integer", "description": "Gets or sets the total number of records available.", "format": "int32"},
  "StartIndex": {"type": "integer", "description": "Gets or sets the index of the first record in Items.", "format": "int32"}
 },
 "additionalProperties": false,
 "description": "Query result container."
}
```

## jellyfin-openapi-12.json → paths → "/Shows/{seriesId}/Seasons" → get

`operationId: "GetSeasons"`. Path parameter `seriesId` (required, uuid).
Same `fields` parameter shape as `/Items` above, plus season-specific
filters (`isSpecialSeason`, `isMissing`, `adjacentTo`) not used by the
client. `200` response schema: `BaseItemDtoQueryResult` (same as `/Items`,
above). Security: `CustomAuthentication: [DefaultAuthorization]`.

## jellyfin-openapi-12.json → paths → "/Shows/{seriesId}/Episodes" → get

`operationId: "GetEpisodes"`. Path parameter `seriesId` (required, uuid).
Also takes `season` (int32) and `seasonId` (uuid) query parameters for
narrowing to one season. `200` response schema: `BaseItemDtoQueryResult`
(same as `/Items`, above). Security: `CustomAuthentication:
[DefaultAuthorization]`.

## jellyfin-openapi-12.json → paths → "/Items/{itemId}" → get

`operationId: "GetItem"`. Parameters: `userId` (query, uuid, optional),
`itemId` (path, required, uuid). `200` response schema: `BaseItemDto` (155
properties total; the ones the client writes are quoted under `POST` below).
Security: `CustomAuthentication: [DefaultAuthorization]`.

## jellyfin-openapi-12.json → paths → "/Items/{itemId}" → post

```json
{
 "tags": ["ItemUpdate"],
 "summary": "Updates an item.",
 "operationId": "UpdateItem",
 "parameters": [
  {"name": "itemId", "in": "path", "description": "The item id.", "required": true, "schema": {"type": "string", "format": "uuid"}}
 ],
 "requestBody": {
  "description": "The new item properties.",
  "content": {
   "application/json": {"schema": {"allOf": [{"$ref": "#/components/schemas/BaseItemDto"}]}},
   "text/json": {"schema": {"allOf": [{"$ref": "#/components/schemas/BaseItemDto"}]}},
   "application/*+json": {"schema": {"allOf": [{"$ref": "#/components/schemas/BaseItemDto"}]}}
  },
  "required": true
 },
 "responses": {"204": {"description": "Item updated."}},
 "security": [{"CustomAuthentication": ["RequiresElevation"]}]
}
```

A `204` with no body on success — this is a full-body echo like Arr's `PUT
/api/v3/{movie|series}/{id}`, not a tag-only editor endpoint; there is no
Jellyfin equivalent of Arr's `.../editor` route. `BaseItemDto` has 155
properties (`additionalProperties` not declared false, so extras are
tolerated, but a dropped field the caller doesn't already have from a prior
`GET` is still a real field lost). The 16 fields the client writes,
individually verified present with these exact types:

```json
{"Name": {"type": "string", "description": "Gets or sets the name.", "nullable": true}}
{"SortName": {"type": "string", "description": "Gets or sets the name of the sort.", "nullable": true}}
{"ForcedSortName": {"type": "string", "nullable": true}}
{"Overview": {"type": "string", "description": "Gets or sets the overview.", "nullable": true}}
{"Genres": {"type": "array", "items": {"type": "string"}, "description": "Gets or sets the genres.", "nullable": true}}
{"Studios": {"type": "array", "items": {"$ref": "#/components/schemas/NameGuidPair"}, "description": "Gets or sets the studios.", "nullable": true}}
{"OfficialRating": {"type": "string", "description": "Gets or sets the official rating.", "nullable": true}}
{"Tags": {"type": "array", "items": {"type": "string"}, "description": "Gets or sets the tags.", "nullable": true}}
{"LockedFields": {"type": "array", "items": {"$ref": "#/components/schemas/MetadataField"}, "description": "Gets or sets the locked fields.", "nullable": true}}
{"ProviderIds": {"type": "object", "additionalProperties": {"type": "string", "nullable": true}, "description": "Gets or sets the provider ids.", "nullable": true}}
{"Path": {"type": "string", "description": "Gets or sets the path.", "nullable": true}}
{"IndexNumber": {"type": "integer", "description": "Gets or sets the index number.", "format": "int32", "nullable": true}}
{"ParentIndexNumber": {"type": "integer", "description": "Gets or sets the parent index number.", "format": "int32", "nullable": true}}
{"SeriesId": {"type": "string", "description": "Gets or sets the series id.", "format": "uuid", "nullable": true}}
{"Id": {"type": "string", "description": "Gets or sets the id.", "format": "uuid"}}
{"Type": {"description": "The base item kind.", "allOf": [{"$ref": "#/components/schemas/BaseItemKind"}]}}
```

Note `Studios` is `NameGuidPair` objects, not plain strings — a studio write
needs at least a `Name`, unlike `Genres`/`Tags` which are plain string
arrays. `Id` and `Type` are non-nullable (no `"nullable": true`); every other
field on this list is nullable.

## jellyfin-openapi-12.json → paths → "/Items/{itemId}/Images/{imageType}" → post

```json
{
 "tags": ["Image"],
 "summary": "Set item image.",
 "operationId": "SetItemImage",
 "parameters": [
  {"name": "itemId", "in": "path", "required": true, "schema": {"type": "string", "format": "uuid"}},
  {"name": "imageType", "in": "path", "required": true, "schema": {"allOf": [{"$ref": "#/components/schemas/ImageType"}]}, "description": "Enum ImageType."}
 ],
 "requestBody": {"content": {"image/*": {"schema": {"type": "string", "format": "binary"}}}},
 "responses": {"204": {"description": "Image saved."}},
 "security": [{"CustomAuthentication": ["RequiresElevation"]}]
}
```

Body content type is `image/*`, and the schema above declares raw binary —
not multipart, not JSON. **That is the spec's claim, not the wire's**: the
live instance answers `500` to a raw-bytes body and wants it base64-encoded
instead, `Content-Type` unchanged (observation, not spec — see item 5 in
"Verified live" above; `JellyfinApi.set_image` is where this is handled).
`ImageType` enum, verbatim (13 values):

```json
{
 "enum": ["Primary", "Art", "Backdrop", "Banner", "Logo", "Thumb", "Disc", "Box", "Screenshot", "Menu", "Chapter", "BoxRear", "Profile"],
 "type": "string",
 "description": "Enum ImageType."
}
```

## jellyfin-openapi-12.json → paths → "/Items/{itemId}/Images/{imageType}" → get

`operationId: "GetItemImage"`. Path parameters `itemId` (uuid) and
`imageType` (`ImageType` enum, above). Also takes an optional `imageIndex`
query parameter (int32) — this is the route called out as having
both an `{imageType}` path segment and an `imageIndex` query parameter,
rather than a single `.../{imageType}/{imageIndex}` path. `200` response:
raw `image/*` bytes, no JSON schema. No `security` block on this operation
(unlike every other route captured here).

## jellyfin-openapi-12.json → paths → "/Items/{itemId}/Images/{imageType}" → delete

`operationId: "DeleteItemImage"`. Same path parameters as `get` above
(`itemId`, `imageType`), plus optional `imageIndex` query parameter (int32).
`204` on success. Security: `CustomAuthentication: [RequiresElevation]`.

## jellyfin-openapi-12.json → paths → "/Items/{itemId}/Images" → get

`operationId: "GetItemImageInfos"`. Path parameter `itemId` (uuid) only.
`200` response: `array` of `ImageInfo`, verbatim:

```json
{
 "type": "object",
 "properties": {
  "ImageType": {"allOf": [{"$ref": "#/components/schemas/ImageType"}], "description": "Gets or sets the type of the image."},
  "ImageIndex": {"type": "integer", "description": "Gets or sets the index of the image.", "format": "int32", "nullable": true},
  "ImageTag": {"type": "string", "description": "Gets or sets the image tag.", "nullable": true},
  "Path": {"type": "string", "description": "Gets or sets the path.", "nullable": true},
  "BlurHash": {"type": "string", "description": "Gets or sets the blurhash.", "nullable": true},
  "Height": {"type": "integer", "description": "Gets or sets the height.", "format": "int32", "nullable": true},
  "Width": {"type": "integer", "description": "Gets or sets the width.", "format": "int32", "nullable": true},
  "Size": {"type": "integer", "description": "Gets or sets the size.", "format": "int64"}
 },
 "additionalProperties": false,
 "description": "Class ImageInfo."
}
```

## jellyfin-openapi-12.json → paths → "/Items/{itemId}/Refresh" → post (abridged)

Abridged: the query parameters' `allOf`/`$ref` mode-schema wrappers are
flattened to their `enum` inline below, and the `401`/`403`/`503` responses
(same shape as every other route captured here) are omitted; only `204` and
`404` are kept.

```json
{
 "tags": ["Library"],
 "summary": "Refreshes metadata for an item.",
 "operationId": "RefreshItem",
 "parameters": [
  {"name": "itemId", "in": "path", "description": "Item id.", "required": true, "schema": {"type": "string", "format": "uuid"}},
  {"name": "metadataRefreshMode", "in": "query", "description": "(Optional) Specifies the metadata refresh mode.", "schema": {"enum": ["None", "ValidationOnly", "Default", "FullRefresh"], "default": "None"}},
  {"name": "imageRefreshMode", "in": "query", "description": "(Optional) Specifies the image refresh mode.", "schema": {"enum": ["None", "ValidationOnly", "Default", "FullRefresh"], "default": "None"}},
  {"name": "replaceAllMetadata", "in": "query", "description": "(Optional) Determines if metadata should be replaced. Only applicable if mode is FullRefresh.", "schema": {"type": "boolean", "default": false}},
  {"name": "replaceAllImages", "in": "query", "description": "(Optional) Determines if images should be replaced. Only applicable if mode is FullRefresh.", "schema": {"type": "boolean", "default": false}},
  {"name": "regenerateTrickplay", "in": "query", "description": "(Optional) Determines if trickplay images should be replaced. Only applicable if mode is FullRefresh.", "schema": {"type": "boolean", "default": false}}
 ],
 "responses": {
  "204": {"description": "Item metadata refresh queued."},
  "404": {"description": "Item to refresh not found."}
 },
 "security": [{"CustomAuthentication": ["RequiresElevation"]}]
}
```

`replaceAllMetadata`/`replaceAllImages`/`regenerateTrickplay` are each documented "Only applicable if mode is FullRefresh" — a `Default`-mode call (this document's `metadataRefreshMode=Default, imageRefreshMode=Default, replaceAllImages=false`) is the routine-scan shape V4 (§11) checks against; `FullRefresh` + `replaceAllImages=true` is the destructive form V4 names but this task does not exercise, to keep the write scope to one deterministic movie. `204` on success, no body — same "queued", not synchronous, shape as the rest of this API's mutating calls.

## MetadataField enum (verbatim)

```json
{
 "enum": ["Cast", "Genres", "ProductionLocations", "Studios", "Tags", "Name", "Overview", "Runtime", "OfficialRating"],
 "type": "string",
 "description": "Enum MetadataFields."
}
```

This is what `BaseItemDto.LockedFields` (above) holds one-or-more of.

## securitySchemes.CustomAuthentication (verbatim)

```json
{
 "CustomAuthentication": {
  "type": "apiKey",
  "description": "API key header parameter",
  "name": "Authorization",
  "in": "header"
 }
}
```

This is the only entry under `components.securitySchemes` — every operation
captured above that declares a `security` block names this one scheme. The
header **value** format is not in the spec; V1 in the design doc (see
"Verified live" above).

The small script used to pull every extract in this document verbatim from
the spec (paths → operationId/parameters/requestBody, and the referenced
response schemas' property names) is banked alongside its output and the
sha256/version check.
