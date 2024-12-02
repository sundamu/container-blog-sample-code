#!/bin/bash

for i in {1..60}
do
  export N=$i
  envsubst < nginx-template.yaml > nginx.yaml
  kubectl delete -f nginx.yaml
done

kubectl delete namespace sample-nginx