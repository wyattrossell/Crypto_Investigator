# Release checklist

Every release ships four things: the code, a changelog entry, the hashed
program listing, and the validation documents. Do them in this order.

1. **Bump the version** in `VERSION`, `app/config.py` (`APP_VERSION`) and
   `packaging/version_info.txt` (the build script rewrites the numeric
   fields of the last one, but keep the strings in step).
2. **CHANGELOG.md** — add a section at the top with *Functionality*,
   *Dependencies* and *Known limitations / problems detected*. Say what was
   verified and what was not.
3. **Regenerate the documents**: `python tools/make_docs.py`. This writes
   `docs/listings/v<version>-full-listing.txt` (every source file,
   discovered automatically) and the two PDFs in `docs/validation/`,
   which embed the listing's SHA-256.
4. **Run the checks** you changed behaviour for (a short live trace, the
   scratch tests, a headless-browser pass over the UI).
5. **Commit and push** (`main`), then tag: `git tag v<version>` and
   `git push origin v<version>`.
6. **Build**: `python tools/build_release.py` → `dist/` holds the portable
   zip and, with Inno Setup installed, the setup executable. Sign the
   executables if the agency has a certificate.
7. **Publish**: `gh release create v<version> dist/*.zip dist/*.exe
   --title "Crypto Investigator v<version>" --notes-file <notes>` (or the
   GitHub web UI). Paste the changelog section as the notes and list the
   SHA-256 of each artefact (`certutil -hashfile <file> SHA256`).
8. **Tell the agencies**: what changed, whether the data folder or keys
   need attention, and the artefact hashes.

Never include `data/investigator.db`, `data/reports` or `data/logs` in
anything that leaves the machine; `.gitignore` and the build script both
exclude them, but check.
