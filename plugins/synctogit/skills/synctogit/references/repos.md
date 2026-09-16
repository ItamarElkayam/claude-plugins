# Repositories and organization access

## Choosing an existing repository

- Current folder unambiguously a RechaviLab repo → use it, no picker
- Offer a secondary "Change repository" action in the review
- Destination unclear → list RechaviLab repos the authenticated user can access. Page through
  the **full** list (`gh repo list RechaviLab --limit …`); never silently show only the first page
- Mark repos where they lack upload permission, and archived ones
- Allow searching a long list. Creation is a secondary action, never a mandatory question
- Show the local working copy's path. Several matches → ask which. Never guess where
  uncommitted work lives
- Selecting another repository switches the working folder. It must never retarget the current
  folder's remote or move its files into the newly selected repository

## Cloning

1. First offer to point at an existing local copy, so no redundant clone is made
2. Otherwise offer to clone and ask where
3. Clone only into a new or empty destination — never overwrite or merge into a populated folder
4. Afterwards, state clearly which folder to work in

## Creating a repository

**Ask for the local folder explicitly. Two options, and the current directory is never a
default:**

1. **Use an existing folder** — the user picks it. Then, before creating anything, show what
   would happen: "38 files ready to upload · 12,400 excluded". A wrong folder is obvious there
2. **Start a new empty folder** — the user gives its name and location

Refuse a home directory, Desktop, Downloads, or a cloud-sync root as the project folder.

Then one compact form:

| Field | Default |
| --- | --- |
| Repository name | Suggested from the folder; editable |
| Visibility | Private |
| Lab access | Whole lab can read and edit |
| Restrict access | Optional; reveal a people/team selector only if used |

Validate name, destination, permissions, and folder before the final review. Prepare
conversions and exclusions before publishing anything.

Final action: "Create repository and sync" — one confirmation authorizes creation, access
setup, first commit, and upload. **Establish the reviewed access before pushing content.** A
permissions failure must never fall back to broader sharing.

No branch, license, template, or commit-message menus in the ordinary flow.

## At runtime

- Act with the user's own permissions; list only repos they can access
- Never grant organization ownership or change base permissions during a sync
- Never broaden access when a grant fails
- Never change an existing repo's sharing just because it differs from the default
- Resolve selected people and teams through GitHub and show clear identities
- Verify intended sharing before uploading
- Creation succeeded but access configuration failed → report the created empty repository and
  the pending setup. Do not delete it, do not upload with the wrong permissions

## Administrator setup (one time, by the organization owner)

- Organization base permissions: None
- A team representing the whole lab; standard repos grant it Write
- Allow intended members to create private repositories
- Decide who manages the lab-wide team and restricted access
- Base permissions must not already grant what a restricted repository is meant to withhold

Two assumptions this flow depends on: the target branch is not protected against a direct push,
and no required-review rule stands between a member and `main`. If either is added, this needs
a pull-request path it does not have.

Optional, and the only real enforcement against someone using another tool: organization
rulesets restricting maximum file size and file paths. That is an administrator decision, not
part of this skill.
