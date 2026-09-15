module.exports = {
  apps: [
    {
      name: "api",
      script: "venv/bin/uvicorn",
      args: "app.main:app --host 0.0.0.0 --port 8000 --workers 4",
      interpreter: "none",
      env: {
        APP_ENV: "production",
      }
    },
    {
      name: "worker",
      script: "venv/bin/python",
      args: "-m app.worker",
      interpreter: "none",
      env: {
        APP_ENV: "production",
      }
    },
    {
      name: "scheduler",
      script: "venv/bin/python",
      args: "-m app.scheduler",
      interpreter: "none",
      env: {
        APP_ENV: "production",
      }
    }
  ]
};
