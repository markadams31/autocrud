import { test, expect } from '@playwright/test'

import { installMockApi } from './mock-api'

test.beforeEach(async ({ page }) => {
  await installMockApi(page)
})

test('sidebar lists the database, schema, and tables', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('DemoDB')).toBeVisible()
  await expect(page.getByRole('button', { name: 'dbo', exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Employee' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Department' })).toBeVisible()
})

test('opening a table renders the grid with rows and resolved FK labels', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()

  // Title + record count
  await expect(page.getByRole('heading', { name: 'Employee' })).toBeVisible()
  await expect(page.getByText('3 records')).toBeVisible()

  // Row data
  await expect(page.getByText('Ada Lovelace')).toBeVisible()
  // FK id 2 -> "Research" resolved via the options endpoint
  await expect(page.getByText('Research')).toBeVisible()
})

test('FK field is a searchable combobox (type to filter, pick a label)', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await page.getByRole('button', { name: 'New record' }).click()

  // The slide-over form opens
  await expect(page.getByRole('heading', { name: 'New record' })).toBeVisible()

  // Department is rendered as a combobox input (not a plain select)
  const fk = page.locator('#field-DepartmentID')
  await expect(fk).toBeVisible()
  await expect(fk).toHaveAttribute('placeholder', 'Search…')

  // Type to filter; only the matching option remains
  await fk.click()
  await fk.fill('Eng')
  const options = page.locator('[data-slot="combobox-item"]')
  await expect(options).toHaveCount(1)
  await expect(options.first()).toHaveText('Engineering')

  // Selecting it fills the input with the human label
  await options.first().click()
  await expect(fk).toHaveValue('Engineering')
})

test('integer field is a number field with working steppers', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await page.getByRole('button', { name: 'New record' }).click()

  const salary = page.locator('#field-Salary')
  await salary.fill('100')
  await page.getByRole('button', { name: 'Increase' }).first().click()
  await expect(salary).toHaveValue('101')
  await page.getByRole('button', { name: 'Decrease' }).first().click()
  await page.getByRole('button', { name: 'Decrease' }).first().click()
  await expect(salary).toHaveValue('99')
})

test('the accent theme can be changed', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Change theme' }).click()
  await page.getByRole('menuitemradio', { name: 'Violet' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-accent', 'violet')
})

test('the authenticated user is shown in the sidebar', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('ada@contoso.com')).toBeVisible()
  await expect(page.getByText('Signed in')).toBeVisible()
})

test('export downloads the displayed rows as CSV', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await expect(page.getByText('Ada Lovelace')).toBeVisible()

  // Everything fits on one page, so Export is a single button (no menu).
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Export' }).click(),
  ])
  expect(download.suggestedFilename()).toBe('dbo.Employee.csv')
  await expect(page.getByText('Exported 3 rows.')).toBeVisible()
})

test('export respects the active filter', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()

  // Filter to a single match, then export — it should export the filtered set.
  await page.getByRole('button', { name: 'Add filter' }).click()
  await page.getByRole('menuitem', { name: 'Full Name' }).click()
  await page.getByLabel('Full Name filter value').fill('Lovelace')
  await expect(page.getByText('Alan Turing')).toBeHidden()

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Export' }).click(),
  ])
  expect(download.suggestedFilename()).toBe('dbo.Employee.csv')
  await expect(page.getByText('Exported 1 row.')).toBeVisible()
})

test('text filter uses contains (not equality)', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()

  await page.getByRole('button', { name: 'Add filter' }).click()
  await page.getByRole('menuitem', { name: 'Full Name' }).click()

  // Default text operator is "contains" — a partial value matches.
  await page.getByLabel('Full Name filter value').fill('Lovelace')
  await expect(page.getByText('Ada Lovelace')).toBeVisible()
  await expect(page.getByText('Alan Turing')).toBeHidden()
})

test('numeric filter supports a greater-than operator', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()

  await page.getByRole('button', { name: 'Add filter' }).click()
  await page.getByRole('menuitem', { name: 'Salary' }).click()

  // Switch the operator from "=" to ">"
  await page.getByLabel('Salary operator').click()
  await page.getByRole('option', { name: '>', exact: true }).click()

  await page.getByLabel('Salary filter value').fill('130000')

  // Only Alan Turing (135000) is above the threshold.
  await expect(page.getByText('Alan Turing')).toBeVisible()
  await expect(page.getByText('Ada Lovelace')).toBeHidden()
  await expect(page.getByText('Grace Hopper')).toBeHidden()
})

test('an expired session is refreshed and the request replayed automatically', async ({
  page,
}) => {
  // Replace the default mock with one whose first row query returns 401.
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await installMockApi(page, { expireRowsOnce: true })

  await page.goto('/')
  // Opening the table fires a row query that 401s once. The app should hit
  // /.auth/refresh and replay the query — no manual navigation, no dead screen.
  const [refreshReq] = await Promise.all([
    page.waitForRequest('**/.auth/refresh'),
    page.getByRole('button', { name: 'Employee' }).click(),
  ])
  expect(refreshReq).toBeTruthy()
  await expect(page.getByText('Ada Lovelace')).toBeVisible()
})

test('selecting a row deletes it instantly with Undo (no confirm dialog)', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await expect(page.getByText('Ada Lovelace')).toBeVisible()

  // Tick the first data row's checkbox → the bulk action bar appears.
  await page.getByRole('checkbox', { name: 'Select row' }).first().click()
  await expect(page.getByText('1 selected')).toBeVisible()

  // An explicit selection deletes immediately with an Undo toast — no modal.
  await page
    .getByRole('region', { name: 'Bulk actions' })
    .getByRole('button', { name: 'Delete' })
    .click()
  await expect(page.getByText(/Deleting 1 record/)).toBeVisible()
  await expect(page.getByRole('row', { name: /Ada Lovelace/ })).toHaveCount(0)
})

test('the header checkbox selects every loaded row for bulk delete', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await expect(page.getByText('Ada Lovelace')).toBeVisible()

  await page.getByRole('checkbox', { name: 'Select all rows on this page' }).click()
  await expect(page.getByText('3 selected')).toBeVisible()

  await page
    .getByRole('region', { name: 'Bulk actions' })
    .getByRole('button', { name: 'Delete' })
    .click()
  await expect(page.getByText(/Deleting 3 records/)).toBeVisible()
  await expect(page.getByText('No records yet')).toBeVisible()
})

test('clearing a selection hides the bulk bar', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await expect(page.getByText('Ada Lovelace')).toBeVisible()

  await page.getByRole('checkbox', { name: 'Select row' }).first().click()
  await expect(page.getByText('1 selected')).toBeVisible()

  await page
    .getByRole('region', { name: 'Bulk actions' })
    .getByRole('button', { name: 'Clear' })
    .click()
  await expect(page.getByText('1 selected')).toBeHidden()
})

test('the header checkbox + bulk edit applies one change to every selected row', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await expect(page.getByText('Ada Lovelace')).toBeVisible()

  // Select every loaded row, then open the bulk-edit panel.
  await page.getByRole('checkbox', { name: 'Select all rows on this page' }).click()
  await expect(page.getByText('3 selected')).toBeVisible()
  await page
    .getByRole('region', { name: 'Bulk actions' })
    .getByRole('button', { name: 'Edit' })
    .click()

  await expect(page.getByRole('heading', { name: 'Edit 3 records' })).toBeVisible()

  // Add the Notes field and set a value applied to all three rows.
  await page.getByRole('button', { name: 'Add field' }).click()
  await page.getByRole('menuitem', { name: 'Notes' }).click()
  await page.locator('#bulk-field-Notes').fill('Reviewed')
  await page.getByRole('button', { name: 'Apply to 3 records' }).click()

  await expect(page.getByText('Updated 3 records.')).toBeVisible()
  // All three rows now show the new note after the grid refetches.
  await expect(page.getByText('Reviewed')).toHaveCount(3)
})

test('bulk edit sets a foreign key on the selected row', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await expect(page.getByText('Ada Lovelace')).toBeVisible()

  await page.getByRole('checkbox', { name: 'Select row' }).first().click()
  await page
    .getByRole('region', { name: 'Bulk actions' })
    .getByRole('button', { name: 'Edit' })
    .click()
  await expect(page.getByRole('heading', { name: 'Edit 1 record' })).toBeVisible()

  // Department is a searchable FK combobox, just like the single-record form.
  await page.getByRole('button', { name: 'Add field' }).click()
  await page.getByRole('menuitem', { name: 'Department' }).click()
  const fk = page.locator('#bulk-field-DepartmentID')
  await fk.click()
  await fk.fill('Finance')
  await page.locator('[data-slot="combobox-item"]').first().click()
  await page.getByRole('button', { name: 'Apply to 1 record' }).click()

  await expect(page.getByText('Updated 1 record.')).toBeVisible()
  // Ada's department now resolves to the new FK label in the grid.
  await expect(page.getByText('Finance')).toBeVisible()
})

test('bulk edit blocks clearing a required field', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await expect(page.getByText('Ada Lovelace')).toBeVisible()

  await page.getByRole('checkbox', { name: 'Select row' }).first().click()
  await page
    .getByRole('region', { name: 'Bulk actions' })
    .getByRole('button', { name: 'Edit' })
    .click()

  // Add the required Full Name field but leave it empty, then try to apply.
  await page.getByRole('button', { name: 'Add field' }).click()
  await page.getByRole('menuitem', { name: 'Full Name' }).click()
  await page.getByRole('button', { name: 'Apply to 1 record' }).click()

  // Client-side guard blocks it: inline error, no success, panel stays open.
  await expect(page.getByText('This field cannot be empty.')).toBeVisible()
  await expect(page.getByText('Updated 1 record.')).toBeHidden()
  await expect(page.getByRole('heading', { name: 'Edit 1 record' })).toBeVisible()
})

test('the CSV import template downloads with the right filename', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await page.getByRole('button', { name: 'Import', exact: true }).click()

  await expect(page.getByRole('heading', { name: 'Import CSV' })).toBeVisible()
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Download template' }).click(),
  ])
  expect(download.suggestedFilename()).toBe('dbo.Employee-template.csv')
})

test('importing a clean CSV creates the rows (FK by label)', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await page.getByRole('button', { name: 'Import', exact: true }).click()

  // DepartmentID is given as human labels — resolved to ids in the preview.
  await page.getByLabel('CSV file').setInputFiles({
    name: 'employees.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(
      'FullName,Salary,DepartmentID,IsActive\n' +
        'Katherine Johnson,90000,Engineering,true\n' +
        'Dorothy Vaughan,95000,Finance,false\n',
    ),
  })

  await expect(page.getByText('Ready to import')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Import 2 rows' })).toBeEnabled()
  await page.getByRole('button', { name: 'Import 2 rows' }).click()

  await expect(page.getByText('Imported 2 records.')).toBeVisible()
  // The new rows appear after the grid refetches, with the FK label resolved.
  await expect(page.getByText('Katherine Johnson')).toBeVisible()
  await expect(page.getByText('Dorothy Vaughan')).toBeVisible()
  await expect(page.getByText('Finance')).toBeVisible()
})

test('a CSV with bad cells is blocked until fixed', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await page.getByRole('button', { name: 'Import', exact: true }).click()

  // Row 1 has a non-numeric salary; row 2 is missing the required Full Name.
  await page.getByLabel('CSV file').setInputFiles({
    name: 'bad.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('FullName,Salary\nAda,notanumber\n,50000\n'),
  })

  await expect(page.getByText('2 errors to fix')).toBeVisible()
  // The specific reasons are spelled out, not just hidden in a tooltip.
  await expect(page.getByText('Expected a whole number.')).toBeVisible()
  await expect(page.getByText('Required.')).toBeVisible()
  // Import stays disabled while the preview has errors.
  await expect(page.getByRole('button', { name: 'Import 2 rows' })).toBeDisabled()
})

test('deleting a row is instant and can be undone', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  // Match the table row specifically (the toast text also contains the name).
  const adaRow = page.getByRole('row', { name: /Ada Lovelace/ })
  await expect(adaRow).toBeVisible()

  // The row action deletes immediately — no confirm modal — and offers Undo.
  // The toast reads "Deleting…" because the server call is still pending.
  await adaRow.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByText(/Deleting Ada Lovelace/)).toBeVisible()
  await expect(page.getByRole('row', { name: /Ada Lovelace/ })).toHaveCount(0)

  // Undo restores the row (no server round-trip happens within the window).
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByRole('row', { name: /Ada Lovelace/ })).toHaveCount(1)
})

test('hides all database-managed columns in one click', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await expect(page.getByText('Ada Lovelace')).toBeVisible()

  // The audit column (CreatedDate, db-managed) is shown to start with.
  await expect(page.getByRole('button', { name: 'Created Date' })).toBeVisible()

  await page.getByRole('button', { name: 'Columns' }).click()
  await page.getByRole('menuitem', { name: 'Hide database-managed columns' }).click()

  // The db-managed columns (identity PK + audit) are gone; editable ones remain.
  await expect(page.getByRole('button', { name: 'Created Date' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Employee ID' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Full Name' })).toBeVisible()
})

test('creating a record submits and shows a success toast', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await page.getByRole('button', { name: 'New record' }).click()

  await page.locator('#field-FullName').fill('Margaret Hamilton')

  // Pick a department via the combobox
  const fk = page.locator('#field-DepartmentID')
  await fk.click()
  await fk.fill('Research')
  await page.locator('[data-slot="combobox-item"]').first().click()

  // Set a salary via the number field
  await page.locator('#field-Salary').fill('150000')

  await page.getByRole('button', { name: 'Create record' }).click()

  await expect(page.getByText('Record created.')).toBeVisible()
  // The new record actually persists: it shows in the grid exactly once and the
  // record count goes 3 → 4 (guards against a silently-dropped create).
  await expect(page.getByRole('row', { name: /Margaret Hamilton/ })).toHaveCount(1)
  await expect(page.getByText('4 records')).toBeVisible()
})

test('toggling a boolean in the edit panel persists', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  const adaRow = page.getByRole('row', { name: /Ada Lovelace/ })
  await expect(adaRow).toBeVisible()
  await expect(adaRow.getByText('Yes', { exact: true })).toBeVisible() // Ada starts active

  // Open her edit panel, flip Is Active off, save. (The id is on the switch's
  // hidden input; the visible, clickable control is the role="switch" element.)
  await adaRow.getByRole('button', { name: 'Edit' }).click()
  await page.getByRole('switch').click()
  await page.getByRole('button', { name: 'Save changes' }).click()

  await expect(page.getByText('Changes saved.')).toBeVisible()
  // The change sticks after the optimistic update reconciles with the refetch.
  await expect(adaRow.getByText('No', { exact: true })).toBeVisible()
})

test('a cell can be edited inline with a double-click', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  const adaRow = page.getByRole('row', { name: /Ada Lovelace/ })
  await expect(adaRow).toBeVisible()

  // Double-click Ada's Notes cell, change the value, commit with Enter.
  await adaRow.getByText('Pioneer.').dblclick()
  const editor = adaRow.getByRole('textbox')
  await editor.fill('Edited inline')
  await editor.press('Enter')

  await expect(adaRow.getByText('Edited inline')).toBeVisible()
})

test('an inline edit on a stale row surfaces a conflict and rolls back', async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await installMockApi(page, { conflictOnWrite: true })

  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  const adaRow = page.getByRole('row', { name: /Ada Lovelace/ })
  await expect(adaRow).toBeVisible()

  // Inline-edit Ada's Notes. The write carries the rowversion as If-Match and
  // the (simulated) concurrent change makes the server reject it with 409.
  await adaRow.getByText('Pioneer.').dblclick()
  const editor = adaRow.getByRole('textbox')
  await editor.fill('Edited inline')
  await editor.press('Enter')

  // The conflict is surfaced and the optimistic edit is rolled back to the
  // server's value (the failed write never landed).
  await expect(page.getByText(/changed by someone else/i)).toBeVisible()
  await expect(adaRow.getByText('Pioneer.')).toBeVisible()
  await expect(page.getByText('Edited inline')).toHaveCount(0)
})

test('a conflicting save in the edit panel shows the conflict and closes the form', async ({
  page,
}) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await installMockApi(page, { conflictOnWrite: true })

  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  const adaRow = page.getByRole('row', { name: /Ada Lovelace/ })
  await expect(adaRow).toBeVisible()

  // Open the edit panel, flip a field, and save into a concurrency conflict.
  await adaRow.getByRole('button', { name: 'Edit' }).click()
  await expect(page.getByRole('heading', { name: 'Edit record' })).toBeVisible()
  await page.getByRole('switch').click()
  await page.getByRole('button', { name: 'Save changes' }).click()

  // The conflict is surfaced and the stale form is dismissed so the user can
  // reopen the latest version.
  await expect(page.getByText(/changed by someone else/i)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Edit record' })).toHaveCount(0)
})

test('sorting a column reorders the rows', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Employee' }).click()
  await expect(page.getByText('Ada Lovelace')).toBeVisible()

  // Ada 120k · Grace 128k · Alan 135k. Ascending puts Ada first; descending Alan.
  await page.getByRole('button', { name: 'Salary' }).click()
  await expect(page.getByRole('row').nth(1)).toContainText('Ada Lovelace')
  await page.getByRole('button', { name: 'Salary' }).click()
  await expect(page.getByRole('row').nth(1)).toContainText('Alan Turing')
})

test('the command palette jumps to a table', async ({ page }) => {
  await page.goto('/')
  // Wait until the sidebar's tables are loaded (so the palette has data).
  await expect(page.getByRole('button', { name: 'Department' })).toBeVisible()

  await page.keyboard.press('Control+k')
  await page.getByPlaceholder('Jump to a table…').fill('Depart')
  await page.keyboard.press('Enter')

  await expect(page.getByRole('heading', { name: 'Department' })).toBeVisible()
})
