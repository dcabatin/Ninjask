# Team pools

Real teams to verify and benchmark the engine on. These files are not part
of the crate or the Python package.

## `vgcpastes-regmc.txt`

766 Reg M-C teams, one packed team per line, from the
[VGCPastes Repository](https://docs.google.com/spreadsheets/d/1axlwmzPA49rYkqXh7zHvAtSP-TKbM0ijGYBPRflLSWw)
("Champions M-C" tab), curated by [VGCPastes](https://x.com/VGCPastes).
The teams were shared publicly by their players; most come with a
tournament result. Thank you to VGCPastes for collecting them, and to every
player who published a team.

- `vgcpastes-regmc.txt.ids`: the sheet's team ID (`MC…`) for each line, so
  any team can be traced to its row, its player and its paste.
- `vgcpastes-regmc.txt.status.tsv`: every paste in the sheet with the
  pinned Showdown's verdict. A team is in the pool when Showdown's
  validator accepts it for Reg M-C; duplicates are dropped.

Snapshot of 4 November 2026: 780 teams in the sheet, 775 legal, 766
distinct. Rebuild it (pastes are cached in `data/derived/vgcpastes/`):

```sh
python scripts/build_vgcpastes_pool.py
```

Use it with the checks, for example
`python scripts/check_reference.py --pool pools/vgcpastes-regmc.txt --games 1000`.
