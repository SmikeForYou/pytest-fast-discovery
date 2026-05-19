use pyo3::prelude::*;
use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

#[pyfunction]
fn discover(
    paths: Vec<String>,
    root: String,
    python_files: Vec<String>,
    python_classes: Vec<String>,
    python_functions: Vec<String>,
    norecursedirs: Vec<String>,
) -> PyResult<(Vec<String>, Vec<String>)> {
    let root_path = PathBuf::from(root);
    let ignored_dirs: HashSet<String> = norecursedirs.into_iter().collect();
    let mut files = Vec::new();

    for path in paths {
        let path = path.split("::").next().unwrap_or(&path);
        let path = PathBuf::from(path);
        let path = if path.is_absolute() {
            path
        } else {
            root_path.join(path)
        };

        if path.is_file() {
            if path.extension().is_some_and(|ext| ext == "py") {
                files.push(path);
            }
        } else if path.is_dir() {
            collect_files(&path, &python_files, &ignored_dirs, &mut files);
        }
    }

    files.sort();
    files.dedup();

    let file_nodeids = files
        .iter()
        .map(|file| relative_node_path(&root_path, file))
        .collect::<Vec<_>>();

    let mut nodeids = Vec::new();

    for (file, relative) in files.iter().zip(file_nodeids.iter()) {
        let Ok(contents) = fs::read_to_string(&file) else {
            continue;
        };
        nodeids.extend(scan_file(
            relative,
            &contents,
            &python_classes,
            &python_functions,
        ));
    }

    Ok((nodeids, file_nodeids))
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(discover, module)?)?;
    Ok(())
}

fn collect_files(
    directory: &Path,
    python_files: &[String],
    ignored_dirs: &HashSet<String>,
    files: &mut Vec<PathBuf>,
) {
    let Ok(entries) = fs::read_dir(directory) else {
        return;
    };
    let mut entries: Vec<PathBuf> = entries.flatten().map(|entry| entry.path()).collect();
    entries.sort();

    for path in entries {
        if path.is_dir() {
            let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
                continue;
            };

            if ignored_dirs.contains(name) {
                continue;
            }

            collect_files(&path, python_files, ignored_dirs, files);
        } else if path.is_file() && is_test_file(&path, python_files) {
            files.push(path);
        }
    }
}

fn is_test_file(path: &Path, python_files: &[String]) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };

    python_files.iter().any(|pattern| glob_match(pattern, name))
}

fn glob_match(pattern: &str, value: &str) -> bool {
    if pattern == value {
        return true;
    }

    let Some(star_index) = pattern.find('*') else {
        return false;
    };
    let prefix = &pattern[..star_index];
    let suffix = &pattern[star_index + 1..];
    value.starts_with(prefix) && value.ends_with(suffix)
}

fn scan_file(
    relative_path: &str,
    contents: &str,
    python_classes: &[String],
    python_functions: &[String],
) -> Vec<String> {
    let mut module_functions = Vec::new();
    let mut classes = Vec::new();
    let mut class_stack: Vec<usize> = Vec::new();
    let mut function_stack: Vec<usize> = Vec::new();
    let mut pending_class: Option<(String, usize)> = None;
    let mut in_triple_single = false;
    let mut in_triple_double = false;

    for line in contents.lines() {
        let trimmed = line.trim_start();
        let was_in_triple = in_triple_single || in_triple_double;
        update_triple_state(trimmed, &mut in_triple_single, &mut in_triple_double);

        if was_in_triple || in_triple_single || in_triple_double || trimmed.is_empty() {
            continue;
        }

        let indent = line.len() - trimmed.len();
        let structural_line = !trimmed.starts_with('@') && !trimmed.starts_with('#');

        if structural_line {
            while function_stack
                .last()
                .is_some_and(|function_indent| indent <= *function_indent)
            {
                function_stack.pop();
            }
        }

        let inside_function = function_stack
            .last()
            .is_some_and(|function_indent| indent > *function_indent);

        if inside_function {
            continue;
        }

        if let Some((signature, class_indent)) = pending_class.take() {
            let signature = format!("{signature} {trimmed}");

            if signature.contains(':') {
                let top_level = class_stack.is_empty();
                if let Some(index) = push_class(
                    &mut classes,
                    &signature,
                    class_indent,
                    top_level,
                    python_classes,
                ) {
                    class_stack.push(index);
                }
                continue;
            }

            pending_class = Some((signature, class_indent));
            continue;
        }

        if structural_line {
            while class_stack
                .last()
                .is_some_and(|index| indent <= classes[*index].indent)
            {
                class_stack.pop();
            }
        }

        if trimmed.starts_with("class ") && !trimmed.contains(':') {
            pending_class = Some((trimmed.to_string(), indent));
            continue;
        }

        if trimmed.starts_with("class ") {
            let top_level = class_stack.is_empty();
            if let Some(index) =
                push_class(&mut classes, trimmed, indent, top_level, python_classes)
            {
                class_stack.push(index);
            }
            continue;
        }

        let Some(name) = function_name(trimmed) else {
            if let Some(index) = class_stack.last().copied() {
                if indent > classes[index].indent && trimmed == "__test__ = False" {
                    classes[index].disabled = true;
                } else if indent > classes[index].indent && trimmed == "__test__ = True" {
                    classes[index].forced = true;
                }
            }

            continue;
        };

        if matches_any(&name, python_functions) {
            match class_stack.last().copied() {
                Some(index) if indent > classes[index].indent => {
                    classes[index].methods.push(name);
                }
                None if indent == 0 => {
                    module_functions.push(name);
                }
                _ => {}
            }
        }

        function_stack.push(indent);
    }

    let mut nodeids = module_functions
        .into_iter()
        .map(|name| format!("{relative_path}::{name}"))
        .collect::<Vec<_>>();

    for index in 0..classes.len() {
        if !classes[index].is_collectable() {
            continue;
        }

        for method in collect_methods(index, &classes, &mut Vec::new()) {
            nodeids.push(format!(
                "{relative_path}::{}::{method}",
                classes[index].name
            ));
        }
    }

    nodeids
}

#[derive(Clone)]
struct ParsedClass {
    name: String,
    bases: Vec<String>,
    indent: usize,
    top_level: bool,
    collectable: bool,
    disabled: bool,
    forced: bool,
    methods: Vec<String>,
}

impl ParsedClass {
    fn is_collectable(&self) -> bool {
        self.top_level && (self.forced || self.collectable) && !self.disabled
    }
}

fn push_class(
    classes: &mut Vec<ParsedClass>,
    signature: &str,
    indent: usize,
    top_level: bool,
    python_classes: &[String],
) -> Option<usize> {
    let Some((name, bases)) = class_info(signature) else {
        return None;
    };

    let collectable =
        matches_any(&name, python_classes) || bases.iter().any(|base| base.ends_with("TestCase"));

    classes.push(ParsedClass {
        name,
        bases,
        indent,
        top_level,
        collectable,
        disabled: false,
        forced: false,
        methods: Vec::new(),
    });
    Some(classes.len() - 1)
}

fn class_info(signature: &str) -> Option<(String, Vec<String>)> {
    if !signature.starts_with("class ") {
        return None;
    }

    let rest = signature.trim_start_matches("class ");
    let name = name_from(rest)?;
    let bases = rest
        .find('(')
        .and_then(|start| rest[start + 1..].find(')').map(|end| (start, end)))
        .map(|(start, end)| base_names(&rest[start + 1..start + 1 + end]))
        .unwrap_or_default();

    Some((name, bases))
}

fn base_names(bases: &str) -> Vec<String> {
    bases
        .split(|character: char| {
            !(character.is_ascii_alphanumeric() || character == '_' || character == '.')
        })
        .filter(|base| !base.is_empty())
        .map(|base| base.rsplit('.').next().unwrap_or(base).to_string())
        .collect()
}

fn collect_methods(
    index: usize,
    classes: &[ParsedClass],
    visited: &mut Vec<String>,
) -> Vec<String> {
    if visited.contains(&classes[index].name) {
        return Vec::new();
    }

    visited.push(classes[index].name.clone());
    let mut methods = Vec::new();

    for base in &classes[index].bases {
        if let Some(base_index) = classes.iter().position(|class| &class.name == base) {
            methods.extend(collect_methods(base_index, classes, visited));
        }
    }

    methods.extend(classes[index].methods.clone());
    methods
}

fn function_name(trimmed: &str) -> Option<String> {
    let rest = if trimmed.starts_with("def ") {
        trimmed.trim_start_matches("def ")
    } else if trimmed.starts_with("async def ") {
        trimmed.trim_start_matches("async def ")
    } else {
        return None;
    };

    name_from(rest)
}

fn name_from(rest: &str) -> Option<String> {
    let end = rest
        .find(|character: char| character == '(' || character == ':' || character.is_whitespace())
        .unwrap_or(rest.len());
    let name = &rest[..end];

    if name.is_empty() {
        None
    } else {
        Some(name.to_string())
    }
}

fn matches_any(name: &str, patterns: &[String]) -> bool {
    patterns.iter().any(|pattern| name_match(pattern, name))
}

fn name_match(pattern: &str, value: &str) -> bool {
    if pattern.contains('*') {
        glob_match(pattern, value)
    } else {
        value.starts_with(pattern)
    }
}

fn relative_node_path(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .components()
        .map(|component| component.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("/")
}

fn update_triple_state(trimmed: &str, in_triple_single: &mut bool, in_triple_double: &mut bool) {
    if trimmed.starts_with('#') {
        return;
    }

    let single_count = trimmed.matches("'''").count();
    let double_count = trimmed.matches("\"\"\"").count();

    if single_count % 2 == 1 {
        *in_triple_single = !*in_triple_single;
    }

    if double_count % 2 == 1 {
        *in_triple_double = !*in_triple_double;
    }
}
