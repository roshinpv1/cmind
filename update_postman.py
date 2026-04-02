import json

with open("CodeMind_API.postman_collection.json", "r") as f:
    collection = json.load(f)

# Find 'index' folder or root item array
items = collection.get("item", [])
index_folder = next((item for item in items if item.get("name") == "index"), None)

new_tests = {
    "name": "E2E Branch Indexing Test",
    "item": [
        {
            "name": "Index Main Branch",
            "event": [{"listen": "test", "script": {"exec": [
                "var data = pm.response.json();",
                "pm.test('Status is 200', function() { pm.response.to.have.status(200); });",
                "pm.environment.set('repo_id_main', data.repo_id);",
                "pm.environment.set('job_id_main', data.job_id);"
            ]}}],
            "request": {
                "method": "POST",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "url": "{{baseUrl}}/api/v1/index",
                "body": {"mode": "raw", "raw": "{\"repo_url\": \"https://github.com/psf/requests.git\", \"branch\": \"main\"}"}
            }
        },
        {
            "name": "Index Remote Spec Branch",
            "event": [{"listen": "test", "script": {"exec": [
                "var data = pm.response.json();",
                "pm.test('Status is 200', function() { pm.response.to.have.status(200); });",
                "pm.environment.set('repo_id_dev', data.repo_id);",
                "pm.test('Repo IDs are unique per branch', function() { pm.expect(data.repo_id).to.not.equal(pm.environment.get('repo_id_main')); });"
            ]}}],
            "request": {
                "method": "POST",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "url": "{{baseUrl}}/api/v1/index",
                "body": {"mode": "raw", "raw": "{\"repo_url\": \"https://github.com/psf/requests.git\", \"branch\": \"v2.31.0\"}"}
            }
        }
    ]
}

if index_folder:
    index_folder["item"].append(new_tests)
else:
    items.append(new_tests)

with open("CodeMind_API.postman_collection.json", "w") as f:
    json.dump(collection, f, indent=2)

print("Updated collection.")
