# kodosumi form elements

This document provides an overview of all user interface elements supported by kodosumi. The elements are organized into three main categories: Input Elements, Action Elements, and Content Elements.

## 1. Input Elements

Input elements are used to collect user data through various input methods.

### Text Input (`InputText`)
- Type: `text`
- Purpose: Single-line text input
- Properties:
  - `name`: Field identifier (required)
  - `label`: Display label
  - `value`: Initial value
  - `required`: Whether the field is mandatory
  - `placeholder`: Placeholder text
  - `size`: Input field size
  - `pattern`: Regex pattern for validation

### Number Input (`InputNumber`)
- Type: `number`
- Purpose: Numeric input with validation
- Properties:
  - All properties from `InputText`
  - `min_value`: Minimum allowed value
  - `max_value`: Maximum allowed value
  - `step`: Step increment for number input

### Text Area (`InputArea`)
- Type: `textarea`
- Purpose: Multi-line text input
- Properties:
  - `name`: Field identifier (required)
  - `label`: Display label
  - `value`: Initial value
  - `required`: Whether the field is mandatory
  - `placeholder`: Placeholder text
  - `rows`: Number of visible text lines
  - `cols`: Width of the text area
  - `max_length`: Maximum number of characters allowed

### Date Input (`InputDate`)
- Type: `date`
- Purpose: Date selection input
- Properties:
  - `name`: Field identifier (required)
  - `label`: Display label
  - `value`: Initial date value
  - `required`: Whether the field is mandatory
  - `placeholder`: Placeholder text
  - `min_date`: Minimum allowed date (YYYY-MM-DD)
  - `max_date`: Maximum allowed date (YYYY-MM-DD)

### Time Input (`InputTime`)
- Type: `time`
- Purpose: Time selection input
- Properties:
  - `name`: Field identifier (required)
  - `label`: Display label
  - `value`: Initial time value
  - `required`: Whether the field is mandatory
  - `placeholder`: Placeholder text
  - `min_time`: Minimum allowed time (HH:MM)
  - `max_time`: Maximum allowed time (HH:MM)
  - `step`: Time increment in seconds

### Date-Time Input (`InputDateTime`)
- Type: `datetime-local`
- Purpose: Combined date and time selection
- Properties:
  - `name`: Field identifier (required)
  - `label`: Display label
  - `value`: Initial date-time value
  - `required`: Whether the field is mandatory
  - `placeholder`: Placeholder text
  - `min_datetime`: Minimum allowed date-time (YYYY-MM-DDTHH:MM)
  - `max_datetime`: Maximum allowed date-time (YYYY-MM-DDTHH:MM)
  - `step`: Time increment in seconds

### Checkbox (`Checkbox`)
- Type: `boolean`
- Purpose: Boolean toggle input
- Properties:
  - `name`: Field identifier (required)
  - `option`: Display text for the checkbox
  - `label`: Optional label
  - `value`: Initial state (true/false)

### Select (`Select`)
- Type: `select`
- Purpose: Dropdown selection
- Properties:
  - `name`: Field identifier (required)
  - `option`: List of `InputOption` objects
  - `label`: Display label
  - `value`: Selected option value

### Input Option (`InputOption`)
- Type: `option`
- Purpose: Individual option for Select elements
- Properties:
  - `name`: Option value (required)
  - `label`: Display text for the option
  - `value`: Whether this option is selected
- Usage: Used within `Select` elements to define available choices

## 2. Action Elements

Action elements are used to trigger form submission or navigation.

### Submit (`Submit`)
- Type: `submit`
- Purpose: Form submission button
- Properties:
  - `text`: Button label text

### Cancel (`Cancel`)
- Type: `cancel`
- Purpose: Cancel form and return to home
- Properties:
  - `text`: Button label text

### Action (`Action`)
- Type: `action`
- Purpose: Custom action button
- Properties:
  - `name`: Action identifier
  - `value`: Action value
  - `text`: Button label text

## 3. Content Elements

Content elements are used to structure and display information within the form.

### HTML (`HTML`)
- Type: `html`
- Purpose: Raw HTML content
- Properties:
  - `text`: HTML content to render

### Markdown (`Markdown`)
- Type: `markdown`
- Purpose: Markdown-formatted content
- Properties:
  - `text`: Markdown content to render
- Features:
  - Supports extra markdown features
  - Code highlighting
  - Table of contents
  - Fenced code blocks

### Break (`Break`)
- Type: `html` (specialized)
- Purpose: Visual spacing element
- Properties: None
- Renders as: `<div class="space"></div>`

### Errors (`Errors`)
- Type: `errors`
- Purpose: Display `_global_` form validation errors
- Properties:
  - `error`: List of error messages
- Features:
  - Global error display
  - Styled error container
  - Bold error text