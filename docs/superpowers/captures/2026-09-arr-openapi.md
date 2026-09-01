# Row 89: Radarr / Sonarr OpenAPI capture

Fetched 2026-09-01 from:

- Radarr: `https://raw.githubusercontent.com/Radarr/Radarr/develop/src/Radarr.Api.V3/openapi.json`
  sha256 `95ea9062485118d6a8abed8250b9bfbf94e4de0f55e9c5611da6805864f9a26`
- Sonarr: `https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/openapi.json`
  sha256 `03108f7f6b20300b84af879392ac44947cd3d3c19c073184e5fbf94e127165f`

Both specs report `info.version: "3.0.0"`. No API key was required to fetch either
file (both are public, unauthenticated GitHub raw content) — the commands below
carry no credential, and none appears in this document.

## What the code depends on (verified below, verbatim)

1. `PUT /api/v3/movie/editor` (Radarr) / `PUT /api/v3/series/editor` (Sonarr) take
   `MovieEditorResource`/`SeriesEditorResource` — `{"movieIds"|"seriesIds": [int],
   "tags": [int], "applyTags": "add"}` — a **tag-only** write. `applyTags` is the
   enum `["add", "remove", "replace"]`.
2. `PUT /api/v3/{movie|series}/{id}` takes the FULL `MovieResource`/`SeriesResource`
   (49 and 45 properties, `additionalProperties: false`, no `required` list). It is
   banked here and deliberately NOT used: a full-body echo is how a dropped field
   becomes a NULLed field on the operator's Arr, and it is one request per member
   instead of one per pass.
3. `DELETE /api/v3/movie/editor` (and `.../series/editor`) takes the **same body**
   as the PUT and deletes the movies/series. Method pinning in the tests is
   therefore load-bearing, not decoration.

All three findings are confirmed verbatim by the extraction below. No banked
block disagrees with the plan — the STOP GATE was not triggered.

---

## radarr openapi.json → paths → "/api/v3/movie/{id}" → put/delete/get

```json
{
 "put": {
  "tags": [
   "Movie"
  ],
  "parameters": [
   {
    "name": "moveFiles",
    "in": "query",
    "schema": {
     "type": "boolean",
     "default": false
    }
   },
   {
    "name": "id",
    "in": "path",
    "required": true,
    "schema": {
     "type": "string"
    }
   }
  ],
  "requestBody": {
   "content": {
    "application/json": {
     "schema": {
      "$ref": "#/components/schemas/MovieResource"
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "OK",
    "content": {
     "application/json": {
      "schema": {
       "$ref": "#/components/schemas/MovieResource"
      }
     }
    }
   }
  }
 },
 "delete": {
  "tags": [
   "Movie"
  ],
  "parameters": [
   {
    "name": "id",
    "in": "path",
    "required": true,
    "schema": {
     "type": "integer",
     "format": "int32"
    }
   },
   {
    "name": "deleteFiles",
    "in": "query",
    "schema": {
     "type": "boolean",
     "default": false
    }
   },
   {
    "name": "addImportExclusion",
    "in": "query",
    "schema": {
     "type": "boolean",
     "default": false
    }
   }
  ],
  "responses": {
   "200": {
    "description": "OK"
   }
  }
 },
 "get": {
  "tags": [
   "Movie"
  ],
  "parameters": [
   {
    "name": "id",
    "in": "path",
    "required": true,
    "schema": {
     "type": "integer",
     "format": "int32"
    }
   }
  ],
  "responses": {
   "200": {
    "description": "OK",
    "content": {
     "text/plain": {
      "schema": {
       "$ref": "#/components/schemas/MovieResource"
      }
     },
     "application/json": {
      "schema": {
       "$ref": "#/components/schemas/MovieResource"
      }
     },
     "text/json": {
      "schema": {
       "$ref": "#/components/schemas/MovieResource"
      }
     }
    }
   }
  }
 }
}
```

## radarr openapi.json → paths → "/api/v3/movie/editor" → put/delete

```json
{
 "put": {
  "tags": [
   "MovieEditor"
  ],
  "requestBody": {
   "content": {
    "application/json": {
     "schema": {
      "$ref": "#/components/schemas/MovieEditorResource"
     }
    },
    "text/json": {
     "schema": {
      "$ref": "#/components/schemas/MovieEditorResource"
     }
    },
    "application/*+json": {
     "schema": {
      "$ref": "#/components/schemas/MovieEditorResource"
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "OK"
   }
  }
 },
 "delete": {
  "tags": [
   "MovieEditor"
  ],
  "requestBody": {
   "content": {
    "application/json": {
     "schema": {
      "$ref": "#/components/schemas/MovieEditorResource"
     }
    },
    "text/json": {
     "schema": {
      "$ref": "#/components/schemas/MovieEditorResource"
     }
    },
    "application/*+json": {
     "schema": {
      "$ref": "#/components/schemas/MovieEditorResource"
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "OK"
   }
  }
 }
}
```

Note: `delete` takes the exact same `MovieEditorResource` request body as `put`.

## radarr openapi.json → paths → "/api/v3/tag" → get/post

```json
{
 "get": {
  "tags": [
   "Tag"
  ],
  "responses": {
   "200": {
    "description": "OK",
    "content": {
     "text/plain": {
      "schema": {
       "type": "array",
       "items": {
        "$ref": "#/components/schemas/TagResource"
       }
      }
     },
     "application/json": {
      "schema": {
       "type": "array",
       "items": {
        "$ref": "#/components/schemas/TagResource"
       }
      }
     },
     "text/json": {
      "schema": {
       "type": "array",
       "items": {
        "$ref": "#/components/schemas/TagResource"
       }
      }
     }
    }
   }
  }
 },
 "post": {
  "tags": [
   "Tag"
  ],
  "requestBody": {
   "content": {
    "application/json": {
     "schema": {
      "$ref": "#/components/schemas/TagResource"
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "OK",
    "content": {
     "text/plain": {
      "schema": {
       "$ref": "#/components/schemas/TagResource"
      }
     },
     "application/json": {
      "schema": {
       "$ref": "#/components/schemas/TagResource"
      }
     },
     "text/json": {
      "schema": {
       "$ref": "#/components/schemas/TagResource"
      }
     }
    }
   }
  }
 }
}
```

## radarr openapi.json → components → schemas → "MovieEditorResource"

```json
{
 "type": "object",
 "properties": {
  "movieIds": {
   "type": "array",
   "items": {
    "type": "integer",
    "format": "int32"
   },
   "nullable": true
  },
  "monitored": {
   "type": "boolean",
   "nullable": true
  },
  "qualityProfileId": {
   "type": "integer",
   "format": "int32",
   "nullable": true
  },
  "minimumAvailability": {
   "$ref": "#/components/schemas/MovieStatusType"
  },
  "rootFolderPath": {
   "type": "string",
   "nullable": true
  },
  "tags": {
   "type": "array",
   "items": {
    "type": "integer",
    "format": "int32"
   },
   "nullable": true
  },
  "applyTags": {
   "$ref": "#/components/schemas/ApplyTags"
  },
  "moveFiles": {
   "type": "boolean"
  },
  "deleteFiles": {
   "type": "boolean"
  },
  "addImportExclusion": {
   "type": "boolean"
  }
 },
 "additionalProperties": false
}
```

## radarr openapi.json → components → schemas → "ApplyTags"

```json
{
 "enum": [
  "add",
  "remove",
  "replace"
 ],
 "type": "string"
}
```

## radarr openapi.json → components → schemas → "TagResource"

```json
{
 "type": "object",
 "properties": {
  "id": {
   "type": "integer",
   "format": "int32"
  },
  "label": {
   "type": "string",
   "nullable": true
  }
 },
 "additionalProperties": false
}
```

## radarr openapi.json → components → schemas → "MovieResource" (required / additionalProperties / tags / property count)

```
required: None additionalProperties: False
tags: {"uniqueItems": true, "type": "array", "items": {"type": "integer", "format": "int32"}, "nullable": true}
property count: 49
```

---

## sonarr openapi.json → paths → "/api/v3/series/{id}" → get/put/delete

```json
{
 "get": {
  "tags": [
   "Series"
  ],
  "parameters": [
   {
    "name": "id",
    "in": "path",
    "required": true,
    "schema": {
     "type": "integer",
     "format": "int32"
    }
   },
   {
    "name": "includeSeasonImages",
    "in": "query",
    "schema": {
     "type": "boolean",
     "default": false
    }
   }
  ],
  "responses": {
   "200": {
    "description": "OK",
    "content": {
     "application/json": {
      "schema": {
       "$ref": "#/components/schemas/SeriesResource"
      }
     }
    }
   }
  }
 },
 "put": {
  "tags": [
   "Series"
  ],
  "parameters": [
   {
    "name": "moveFiles",
    "in": "query",
    "schema": {
     "type": "boolean",
     "default": false
    }
   },
   {
    "name": "id",
    "in": "path",
    "required": true,
    "schema": {
     "type": "string"
    }
   }
  ],
  "requestBody": {
   "content": {
    "application/json": {
     "schema": {
      "$ref": "#/components/schemas/SeriesResource"
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "OK",
    "content": {
     "application/json": {
      "schema": {
       "$ref": "#/components/schemas/SeriesResource"
      }
     }
    }
   }
  }
 },
 "delete": {
  "tags": [
   "Series"
  ],
  "parameters": [
   {
    "name": "id",
    "in": "path",
    "required": true,
    "schema": {
     "type": "integer",
     "format": "int32"
    }
   },
   {
    "name": "deleteFiles",
    "in": "query",
    "schema": {
     "type": "boolean",
     "default": false
    }
   },
   {
    "name": "addImportListExclusion",
    "in": "query",
    "schema": {
     "type": "boolean",
     "default": false
    }
   }
  ],
  "responses": {
   "200": {
    "description": "OK"
   }
  }
 }
}
```

## sonarr openapi.json → paths → "/api/v3/series/editor" → put/delete

```json
{
 "put": {
  "tags": [
   "SeriesEditor"
  ],
  "requestBody": {
   "content": {
    "application/json": {
     "schema": {
      "$ref": "#/components/schemas/SeriesEditorResource"
     }
    },
    "text/json": {
     "schema": {
      "$ref": "#/components/schemas/SeriesEditorResource"
     }
    },
    "application/*+json": {
     "schema": {
      "$ref": "#/components/schemas/SeriesEditorResource"
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "OK"
   }
  }
 },
 "delete": {
  "tags": [
   "SeriesEditor"
  ],
  "requestBody": {
   "content": {
    "application/json": {
     "schema": {
      "$ref": "#/components/schemas/SeriesEditorResource"
     }
    },
    "text/json": {
     "schema": {
      "$ref": "#/components/schemas/SeriesEditorResource"
     }
    },
    "application/*+json": {
     "schema": {
      "$ref": "#/components/schemas/SeriesEditorResource"
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "OK"
   }
  }
 }
}
```

Note: `delete` takes the exact same `SeriesEditorResource` request body as `put`.

## sonarr openapi.json → paths → "/api/v3/tag" → get/post

```json
{
 "get": {
  "tags": [
   "Tag"
  ],
  "responses": {
   "200": {
    "description": "OK",
    "content": {
     "application/json": {
      "schema": {
       "type": "array",
       "items": {
        "$ref": "#/components/schemas/TagResource"
       }
      }
     }
    }
   }
  }
 },
 "post": {
  "tags": [
   "Tag"
  ],
  "requestBody": {
   "content": {
    "application/json": {
     "schema": {
      "$ref": "#/components/schemas/TagResource"
     }
    }
   }
  },
  "responses": {
   "200": {
    "description": "OK",
    "content": {
     "text/plain": {
      "schema": {
       "$ref": "#/components/schemas/TagResource"
      }
     },
     "application/json": {
      "schema": {
       "$ref": "#/components/schemas/TagResource"
      }
     },
     "text/json": {
      "schema": {
       "$ref": "#/components/schemas/TagResource"
      }
     }
    }
   }
  }
 }
}
```

## sonarr openapi.json → components → schemas → "SeriesEditorResource"

```json
{
 "type": "object",
 "properties": {
  "seriesIds": {
   "type": "array",
   "items": {
    "type": "integer",
    "format": "int32"
   },
   "nullable": true
  },
  "monitored": {
   "type": "boolean",
   "nullable": true
  },
  "monitorNewItems": {
   "$ref": "#/components/schemas/NewItemMonitorTypes"
  },
  "qualityProfileId": {
   "type": "integer",
   "format": "int32",
   "nullable": true
  },
  "seriesType": {
   "$ref": "#/components/schemas/SeriesTypes"
  },
  "seasonFolder": {
   "type": "boolean",
   "nullable": true
  },
  "rootFolderPath": {
   "type": "string",
   "nullable": true
  },
  "tags": {
   "type": "array",
   "items": {
    "type": "integer",
    "format": "int32"
   },
   "nullable": true
  },
  "applyTags": {
   "$ref": "#/components/schemas/ApplyTags"
  },
  "moveFiles": {
   "type": "boolean"
  },
  "deleteFiles": {
   "type": "boolean"
  },
  "addImportListExclusion": {
   "type": "boolean"
  }
 },
 "additionalProperties": false
}
```

## sonarr openapi.json → components → schemas → "ApplyTags"

```json
{
 "enum": [
  "add",
  "remove",
  "replace"
 ],
 "type": "string"
}
```

## sonarr openapi.json → components → schemas → "TagResource"

```json
{
 "type": "object",
 "properties": {
  "id": {
   "type": "integer",
   "format": "int32"
  },
  "label": {
   "type": "string",
   "nullable": true
  }
 },
 "additionalProperties": false
}
```

## sonarr openapi.json → components → schemas → "SeriesResource" (required / additionalProperties / tags / property count)

```
required: None additionalProperties: False
tags: {"uniqueItems": true, "type": "array", "items": {"type": "integer", "format": "int32"}, "nullable": true}
property count: 45
```
