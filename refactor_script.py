
import os

def replace_in_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        new_content = content.replace('skill', 'playbook')
        new_content = new_content.replace('Skill', 'Playbook')
        new_content = new_content.replace('SKILL', 'PLAYBOOK')
        
        if content != new_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Updated: {filepath}")
    except UnicodeDecodeError:
        print(f"Skipping binary file: {filepath}")
    except Exception as e:
        print(f"Error processing {filepath}: {e}")

def process_directory(directory):
    for root, dirs, files in os.walk(directory):
        if "__pycache__" in root or ".git" in root:
            continue
        for file in files:
            filepath = os.path.join(root, file)
            replace_in_file(filepath)

if __name__ == "__main__":
    print("Starting refactor...")
    process_directory("src/codemind")
    process_directory("tests")
    process_directory("playbooks") 
    replace_in_file("README.md")
    print("Refactor complete.")
