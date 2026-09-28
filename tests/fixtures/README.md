# Test fixtures

All three files are trimmed copies of recipe blocks captured from a signed-in
LinkedIn Learning session (`_recon_out/course_page.html`, capture of
2026-09-28; that file is *not* committed). Course data is server-rendered into
hidden `<code style="display: none" id="bpr-guid-NNNNN">{...}</code>` blocks, so
each fixture is one whole block — `data`, `meta` and `included` — exactly as
`li.mapping.find_recipe_blocks` returns it after decoding the HTML escapes
(`&amp;` in URLs, `&lt;` in descriptions).

| File | Source block | Contents |
| --- | --- | --- |
| `course_recipe.json` | `bpr-guid-12018` (recipe key `data.data.coursesBySlug`) | Whole `included` array (96 entities: 1 Course, 7 Sections, 18 Videos, 5 Articles, 2 Authors, 3 Skills, 8 Profiles, plus bookmarks/interaction-status/social entities) and the Course entity's `contentsDerived` outline. |
| `video_recipe.json` | `bpr-guid-12019` (recipe key `data.data.videosBySlugs`) | Whole `included` array (14 entities) with the Video's `presentationDerived.videoPlay.videoPlayMetadata.progressiveStreams` (720p, 1080p, 640p), its WebVTT `transcripts` list, and the `Transcript` entity whose `lines` `map_video` resolves through `*transcriptsDerived`. |
| `video_720_only.json` | derived from `video_recipe.json` | Same block with the 1080p and 640p progressive streams deleted, leaving only 720p. |

Both blocks carry their entity array at the top level (`payload["included"]`),
not under `payload["data"]["data"]`; only the recipe itself is nested that deep.
`li.mapping` therefore prefers a top-level `included` and falls back to searching
the parsed structure only when the top level holds no usable array.

## What was trimmed

No real course content may live in this repository, so the following was
rewritten in place — entities were never deleted and no URN was rewritten, so
every `*`-reference in both blocks still resolves:

- Course title → `Course A`, slug → `a`; description and objectives → generic
  placeholders.
- Section titles → `Section 1` … `Section 7`.
- Video titles → `Video 1` … `Video 18`, slugs → `v1` … `v18` (outline order;
  the video block is the course's first video, so it is `Video 1`/`v1` there
  too).
- Article titles → `Article 1` … `Article 5`, every article slug → `article`
  (so a mapping bug that coerces articles into videos is detectable).
- Author slugs → `author-one`, `author-two`; biographies → generic text.
- Profile display names, first/last names, headlines, public URLs → `Member N`,
  `Family N`, `Headline N`, `/in/member-N`.
- Skill names → `Skill 1` … `Skill 3` (one position shared by both blocks).
- Transcript captions → `Caption 1.` … `Caption 30.`; course description →
  `<p>Course A description.</p>`.
- Every `expiresAt` → the fixed integer `1790000000000` (96 occurrences in the
  course block, 14 in the video block).

Structural values are untouched: recipe `$type` strings, `cachingKey`s, URNs,
thumbnails, stream/caption URLs and enums (`SECOND`, `SUBSCRIBED`, …). The
Provider's name `LinkedIn` is the platform itself and was kept.

## Structural notes

- **`Section 2` in `course_recipe.json` is SYNTHETIC, and the file says so itself.**
  The real capture has no article-only section: all 7 sections contain at
  least one video, so the video-less `Section 2` shape was manufactured to
  satisfy the required test (`any(not ch.videos for ch in chapters)`). The
  fixture therefore carries a top-level `"synthetic"` marker (first key, right
  before `"data"`) so a reader who opens only the JSON is told: `Section 2`
  keeps only its article item; the 3 video items removed from that section's
  inline `items` list leave their Video entities in `included`, still fully
  resolvable (15 of 18 videos remain referenced by the outline). No entity was
  deleted and no URN was rewritten. Everything else in the three fixtures is
  captured shape, trimmed only for content.
- `Section 1` is the capture's Introduction section: one video item, which is
  why `chapters[0].videos` is non-empty.
- Verification performed after building: zero original strings stored under
  any content key survive in any fixture, and every `*`-reference across both
  payloads resolves to an entity in the same payload.
