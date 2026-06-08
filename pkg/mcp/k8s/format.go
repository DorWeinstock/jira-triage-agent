package k8s

import (
	"fmt"
	"strings"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	sigsyaml "sigs.k8s.io/yaml"
)

// ResourceInfo holds GVR and scope information for a K8s resource type.
type ResourceInfo struct {
	GVR        schema.GroupVersionResource
	Namespaced bool
}

// resourceInfoMap maps resource type strings (plural, singular, short) to GVR and scope.
var resourceInfoMap = map[string]ResourceInfo{
	// Core (namespaced)
	"pods":                    {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "pods"}, Namespaced: true},
	"pod":                     {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "pods"}, Namespaced: true},
	"services":                {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "services"}, Namespaced: true},
	"service":                 {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "services"}, Namespaced: true},
	"svc":                     {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "services"}, Namespaced: true},
	"configmaps":              {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "configmaps"}, Namespaced: true},
	"configmap":               {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "configmaps"}, Namespaced: true},
	"cm":                      {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "configmaps"}, Namespaced: true},
	"secrets":                 {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "secrets"}, Namespaced: true},
	"secret":                  {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "secrets"}, Namespaced: true},
	"endpoints":               {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "endpoints"}, Namespaced: true},
	"ep":                      {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "endpoints"}, Namespaced: true},
	"serviceaccounts":         {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "serviceaccounts"}, Namespaced: true},
	"serviceaccount":          {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "serviceaccounts"}, Namespaced: true},
	"sa":                      {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "serviceaccounts"}, Namespaced: true},
	"persistentvolumeclaims":  {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "persistentvolumeclaims"}, Namespaced: true},
	"persistentvolumeclaim":   {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "persistentvolumeclaims"}, Namespaced: true},
	"pvcs":                    {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "persistentvolumeclaims"}, Namespaced: true},
	"pvc":                     {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "persistentvolumeclaims"}, Namespaced: true},

	// Core (cluster-scoped)
	"nodes":              {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "nodes"}, Namespaced: false},
	"node":               {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "nodes"}, Namespaced: false},
	"namespaces":         {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "namespaces"}, Namespaced: false},
	"namespace":          {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "namespaces"}, Namespaced: false},
	"ns":                 {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "namespaces"}, Namespaced: false},
	"persistentvolumes":  {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "persistentvolumes"}, Namespaced: false},
	"persistentvolume":   {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "persistentvolumes"}, Namespaced: false},
	"pvs":               {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "persistentvolumes"}, Namespaced: false},
	"pv":                {GVR: schema.GroupVersionResource{Group: "", Version: "v1", Resource: "persistentvolumes"}, Namespaced: false},

	// Apps (namespaced)
	"deployments":  {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "deployments"}, Namespaced: true},
	"deployment":   {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "deployments"}, Namespaced: true},
	"deploy":       {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "deployments"}, Namespaced: true},
	"statefulsets":  {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "statefulsets"}, Namespaced: true},
	"statefulset":   {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "statefulsets"}, Namespaced: true},
	"sts":           {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "statefulsets"}, Namespaced: true},
	"daemonsets":    {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "daemonsets"}, Namespaced: true},
	"daemonset":     {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "daemonsets"}, Namespaced: true},
	"ds":            {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "daemonsets"}, Namespaced: true},
	"replicasets":   {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "replicasets"}, Namespaced: true},
	"replicaset":    {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "replicasets"}, Namespaced: true},
	"rs":            {GVR: schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "replicasets"}, Namespaced: true},

	// Batch (namespaced)
	"jobs":     {GVR: schema.GroupVersionResource{Group: "batch", Version: "v1", Resource: "jobs"}, Namespaced: true},
	"job":      {GVR: schema.GroupVersionResource{Group: "batch", Version: "v1", Resource: "jobs"}, Namespaced: true},
	"cronjobs": {GVR: schema.GroupVersionResource{Group: "batch", Version: "v1", Resource: "cronjobs"}, Namespaced: true},
	"cronjob":  {GVR: schema.GroupVersionResource{Group: "batch", Version: "v1", Resource: "cronjobs"}, Namespaced: true},
	"cj":       {GVR: schema.GroupVersionResource{Group: "batch", Version: "v1", Resource: "cronjobs"}, Namespaced: true},

	// Networking (namespaced)
	"ingresses":       {GVR: schema.GroupVersionResource{Group: "networking.k8s.io", Version: "v1", Resource: "ingresses"}, Namespaced: true},
	"ingress":         {GVR: schema.GroupVersionResource{Group: "networking.k8s.io", Version: "v1", Resource: "ingresses"}, Namespaced: true},
	"ing":             {GVR: schema.GroupVersionResource{Group: "networking.k8s.io", Version: "v1", Resource: "ingresses"}, Namespaced: true},
	"networkpolicies": {GVR: schema.GroupVersionResource{Group: "networking.k8s.io", Version: "v1", Resource: "networkpolicies"}, Namespaced: true},
	"networkpolicy":   {GVR: schema.GroupVersionResource{Group: "networking.k8s.io", Version: "v1", Resource: "networkpolicies"}, Namespaced: true},
	"netpol":          {GVR: schema.GroupVersionResource{Group: "networking.k8s.io", Version: "v1", Resource: "networkpolicies"}, Namespaced: true},

	// Storage (cluster-scoped)
	"storageclasses": {GVR: schema.GroupVersionResource{Group: "storage.k8s.io", Version: "v1", Resource: "storageclasses"}, Namespaced: false},
	"storageclass":   {GVR: schema.GroupVersionResource{Group: "storage.k8s.io", Version: "v1", Resource: "storageclasses"}, Namespaced: false},
	"sc":             {GVR: schema.GroupVersionResource{Group: "storage.k8s.io", Version: "v1", Resource: "storageclasses"}, Namespaced: false},

	// Autoscaling (namespaced)
	"horizontalpodautoscalers": {GVR: schema.GroupVersionResource{Group: "autoscaling", Version: "v2", Resource: "horizontalpodautoscalers"}, Namespaced: true},
	"hpa":                      {GVR: schema.GroupVersionResource{Group: "autoscaling", Version: "v2", Resource: "horizontalpodautoscalers"}, Namespaced: true},
}

// resolveGVR maps a resource type string to its GVR and scope info.
func resolveGVR(resourceType string) (ResourceInfo, error) {
	info, ok := resourceInfoMap[strings.ToLower(resourceType)]
	if !ok {
		return ResourceInfo{}, fmt.Errorf(
			"unknown resource type %q. Supported: %s",
			resourceType, supportedResourceTypes(),
		)
	}
	return info, nil
}

// supportedResourceTypes returns a sorted, deduplicated list of supported resource names.
func supportedResourceTypes() string {
	seen := make(map[string]struct{})
	for k := range resourceInfoMap {
		seen[k] = struct{}{}
	}
	return strings.Join(sortedKeys(seen), ", ")
}

// sanitizeResource strips noisy metadata fields and redacts secret data.
// CRITICAL: For Secrets, data values are redacted BEFORE YAML serialization.
func sanitizeResource(obj map[string]interface{}) map[string]interface{} {
	stripNoisyMetadata(obj)

	kind, _ := obj["kind"].(string)
	if kind == "Secret" {
		redactSecretData(obj)
	}

	return obj
}

// stripNoisyMetadata removes verbose metadata fields that add noise without
// diagnostic value: managedFields, generation, resourceVersion, uid,
// and the last-applied-configuration annotation.
func stripNoisyMetadata(obj map[string]interface{}) {
	meta, ok := obj["metadata"].(map[string]interface{})
	if !ok {
		return
	}

	delete(meta, "managedFields")
	delete(meta, "generation")
	delete(meta, "resourceVersion")
	delete(meta, "uid")

	annotations, ok := meta["annotations"].(map[string]interface{})
	if ok {
		delete(annotations, "kubectl.kubernetes.io/last-applied-configuration")
		if len(annotations) == 0 {
			delete(meta, "annotations")
		}
	}
}

// redactSecretData replaces .data values with "<REDACTED>" and removes .stringData.
// Key names are preserved for debugging.
func redactSecretData(obj map[string]interface{}) {
	if data, ok := obj["data"].(map[string]interface{}); ok {
		for key := range data {
			data[key] = "<REDACTED>"
		}
	}
	delete(obj, "stringData")
}

// formatResourceYAML formats a single unstructured resource as clean YAML.
// Noisy metadata is stripped and secret data is redacted before serialization.
func formatResourceYAML(item *unstructured.Unstructured) string {
	obj := item.DeepCopy().Object
	sanitizeResource(obj)

	yamlBytes, err := sigsyaml.Marshal(obj)
	if err != nil {
		return fmt.Sprintf("# Error marshaling resource to YAML: %v\n", err)
	}

	return string(yamlBytes)
}

// formatResourceListYAML formats a list of unstructured resources as multi-document YAML.
// Each resource is separated by "---". Empty lists return a descriptive message.
func formatResourceListYAML(items []unstructured.Unstructured) string {
	if len(items) == 0 {
		return "No resources found\n"
	}

	var result strings.Builder
	result.WriteString(fmt.Sprintf("# %d resource(s)\n", len(items)))

	for i := range items {
		if i > 0 {
			result.WriteString("---\n")
		}
		result.WriteString(formatResourceYAML(&items[i]))
	}

	return result.String()
}
