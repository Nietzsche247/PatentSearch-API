env=${1:-production}
export VERSION=$(git symbolic-ref -q --short HEAD | cut -d '/' -f 1 || git describe --tags --exact-match)
export ENV="$env"
