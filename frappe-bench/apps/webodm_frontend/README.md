### WebODM Frontend

Vue.js frontend for WebODM Frappe app

### Installation

`webodm_frontend` lives inside the `g20-daas` monorepo (it is not a standalone
repo), so it is installed from the monorepo checkout rather than via `bench get-app`:

```bash
cd frappe-bench
bench --site <site> install-app webodm_frontend
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/webodm_frontend
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
