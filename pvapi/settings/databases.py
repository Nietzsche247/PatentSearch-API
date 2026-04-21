# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases
import environ

env = environ.Env(
    # set casting, default value
    DEBUG=(bool, False)
)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("MYSQL_DATABASE"),
        "USER": env("MYSQL_USER"),
        "PASSWORD": env("MYSQL_PASSWORD"),
        "HOST": env("MYSQL_HOST"),
        "PORT": env.int("MYSQL_PORT", 3306),
    }
}

ELASTICSEARCH = {
    "default": {
        "host": env("ELASTIC_HOST"),
        "port": env.int("ELASTIC_PORT", 9243),
        "username": env("ELASTIC_USER"),
        "password": env("ELASTIC_PASSWORD"),
        "timeout": env.int("ELASTIC_TIMEOUT", 60),
    },
}
