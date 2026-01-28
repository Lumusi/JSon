#!/usr/bin/env python3
"""
Java API Extractor for AI Coding Agents - Enhanced Version
Parses Java files and creates compact, token-efficient API documentation.
Includes deprecation information, usage examples, and cross-references.
"""

import os
import re
import json
import argparse
import signal
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Tuple, Set
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from threading import Lock
import traceback


# Global for tracking current file (for Ctrl+C handler)
current_files_lock = Lock()
current_files = {}


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully and show what files were being processed."""
    print("\n\n=== Interrupted! ===")
    with current_files_lock:
        if current_files:
            print("Files that were being processed:")
            for thread_id, file_path in current_files.items():
                print(f"  - {file_path}")
    sys.exit(1)


signal.signal(signal.SIGINT, signal_handler)


@dataclass
class DeprecationInfo:
    """Information about a deprecated element."""
    deprecated: bool = False
    since: str = ""
    for_removal: bool = False
    replacement: str = ""
    reason: str = ""


@dataclass
class UsageExample:
    """Code example from JavaDoc."""
    code: str
    description: str = ""


@dataclass
class Parameter:
    name: str
    type: str
    description: str = ""


@dataclass
class Method:
    name: str
    returns: str
    params: List[Parameter] = field(default_factory=list)
    description: str = ""
    modifiers: List[str] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)
    deprecation: Optional[DeprecationInfo] = None
    throws: List[str] = field(default_factory=list)
    return_description: str = ""
    see_also: List[str] = field(default_factory=list)
    examples: List[UsageExample] = field(default_factory=list)


@dataclass
class Field:
    name: str
    type: str
    modifiers: List[str] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)
    description: str = ""
    deprecation: Optional[DeprecationInfo] = None
    see_also: List[str] = field(default_factory=list)


@dataclass
class JavaClass:
    name: str
    package: str = ""
    class_type: str = "class"
    extends: Optional[str] = None
    implements: List[str] = field(default_factory=list)
    description: str = ""
    annotations: List[str] = field(default_factory=list)
    modifiers: List[str] = field(default_factory=list)
    fields: List[Field] = field(default_factory=list)
    constructors: List[Method] = field(default_factory=list)
    methods: List[Method] = field(default_factory=list)
    enum_values: List[str] = field(default_factory=list)
    deprecation: Optional[DeprecationInfo] = None
    see_also: List[str] = field(default_factory=list)
    examples: List[UsageExample] = field(default_factory=list)


class JavaDocParser:
    """Parser for JavaDoc comments with enhanced extraction."""
    
    DEPRECATED_TAG_PATTERN = re.compile(
        r'@deprecated\s+(.*?)(?=\n\s*\*\s*@|\s*\*/)',
        re.DOTALL | re.IGNORECASE
    )
    
    LINK_PATTERN = re.compile(r'\{@link\s+([^}]+)\}')
    CODE_PATTERN = re.compile(r'\{@code\s+([^}]+)\}')
    
    # Pattern for code blocks in JavaDoc
    PRE_CODE_PATTERN = re.compile(
        r'<pre>(?:<code>)?\s*(.*?)\s*(?:</code>)?</pre>',
        re.DOTALL | re.IGNORECASE
    )
    
    # Pattern for {@snippet} (Java 18+) or code fences
    SNIPPET_PATTERN = re.compile(
        r'\{@snippet\s*:\s*(.*?)\}',
        re.DOTALL
    )
    
    # Pattern for example sections
    EXAMPLE_SECTION_PATTERN = re.compile(
        r'(?:Example|Usage|Sample)s?:?\s*(?:<pre>|{@code|\n\s*\*\s*```)(.*?)(?:</pre>|}|```)',
        re.DOTALL | re.IGNORECASE
    )
    
    REPLACEMENT_PATTERNS = [
        re.compile(r'use\s+(?:\{@link\s+)?([^\s}]+(?:#\w+)?)\}?\s+instead', re.IGNORECASE),
        re.compile(r'replaced\s+by\s+(?:\{@link\s+)?([^\s}]+(?:#\w+)?)\}?', re.IGNORECASE),
        re.compile(r'(?:^|[.\s])see\s+(?:\{@link\s+)?([^\s}]+(?:#\w+)?)\}?', re.IGNORECASE),
        re.compile(r'prefer\s+(?:\{@link\s+)?([^\s}]+(?:#\w+)?)\}?', re.IGNORECASE),
        re.compile(r'(?:should|please|must)?\s*use\s+(?:\{@link\s+)?([^\s}]+(?:#\w+)?)\}?', re.IGNORECASE),
        re.compile(r'switch\s+to\s+(?:\{@link\s+)?([^\s}]+(?:#\w+)?)\}?', re.IGNORECASE),
        re.compile(r'migrate\s+to\s+(?:\{@link\s+)?([^\s}]+(?:#\w+)?)\}?', re.IGNORECASE),
        re.compile(r'\{@link\s+([^}]+)\}\s+should\s+be\s+used', re.IGNORECASE),
        re.compile(r'superseded\s+by\s+(?:\{@link\s+)?([^\s}]+(?:#\w+)?)\}?', re.IGNORECASE),
    ]
    
    PARAM_PATTERN = re.compile(
        r'@param\s+(\w+)\s+(.*?)(?=\n\s*\*\s*@|\s*\*/)',
        re.DOTALL
    )
    
    RETURN_PATTERN = re.compile(
        r'@return\s+(.*?)(?=\n\s*\*\s*@|\s*\*/)',
        re.DOTALL
    )
    
    THROWS_PATTERN = re.compile(
        r'@(?:throws|exception)\s+(\w+)\s+(.*?)(?=\n\s*\*\s*@|\s*\*/)',
        re.DOTALL
    )
    
    SINCE_PATTERN = re.compile(r'@since\s+([\d.]+|[\w\s]+?)(?=\n\s*\*\s*@|\s*\*/)', re.DOTALL)
    
    # Enhanced @see pattern to capture all references
    SEE_PATTERN = re.compile(
        r'@see\s+(?:#(\w+(?:\([^)]*\))?)|(?:\{@link\s+)?([^\s}]+(?:#\w+(?:\([^)]*\))?)?)\}?)',
        re.IGNORECASE
    )
    
    @classmethod
    def parse_javadoc(cls, javadoc: Optional[str]) -> Dict[str, Any]:
        """Parse a complete JavaDoc comment and extract all information."""
        result = {
            'description': '',
            'params': {},
            'returns': '',
            'throws': [],
            'deprecation': None,
            'since': '',
            'see': [],
            'examples': []
        }
        
        if not javadoc:
            return result
        
        # Extract main description (before any @ tags)
        desc_match = re.search(
            r'/\*\*\s*\n?\s*\*?\s*(.*?)(?:\n\s*\*\s*@|\s*\*/)',
            javadoc,
            re.DOTALL
        )
        if desc_match:
            result['description'] = cls._clean_javadoc_text(desc_match.group(1))
        
        # Extract @param tags
        for match in cls.PARAM_PATTERN.finditer(javadoc):
            param_name = match.group(1)
            param_desc = cls._clean_javadoc_text(match.group(2))
            result['params'][param_name] = param_desc
        
        # Extract @return tag
        return_match = cls.RETURN_PATTERN.search(javadoc)
        if return_match:
            result['returns'] = cls._clean_javadoc_text(return_match.group(1))
        
        # Extract @throws tags
        for match in cls.THROWS_PATTERN.finditer(javadoc):
            exception_type = match.group(1)
            exception_desc = cls._clean_javadoc_text(match.group(2))
            result['throws'].append({
                'type': exception_type,
                'description': exception_desc
            })
        
        # Extract @since tag
        since_match = cls.SINCE_PATTERN.search(javadoc)
        if since_match:
            result['since'] = cls._clean_javadoc_text(since_match.group(1))
        
        # Extract ALL @see tags
        for match in cls.SEE_PATTERN.finditer(javadoc):
            # Group 1 is for #methodName, Group 2 is for ClassName#method
            see_ref = match.group(1) or match.group(2)
            if see_ref:
                see_ref = cls._clean_javadoc_text(see_ref)
                if see_ref and see_ref not in result['see']:
                    result['see'].append(see_ref)
        
        # Extract code examples
        result['examples'] = cls._extract_examples(javadoc)
        
        # Extract deprecation info
        result['deprecation'] = cls.parse_deprecation(javadoc)
        
        return result
    
    @classmethod
    def _extract_examples(cls, javadoc: str) -> List[Dict[str, str]]:
        """Extract code examples from JavaDoc."""
        examples = []
        
        # Extract <pre><code> blocks
        for match in cls.PRE_CODE_PATTERN.finditer(javadoc):
            code = match.group(1).strip()
            code = cls._clean_code_block(code)
            if code and len(code) > 10:  # Skip trivial examples
                examples.append({'code': code})
        
        # Extract {@snippet} blocks (Java 18+)
        for match in cls.SNIPPET_PATTERN.finditer(javadoc):
            code = match.group(1).strip()
            code = cls._clean_code_block(code)
            if code:
                examples.append({'code': code})
        
        # Extract "Example:" sections
        for match in cls.EXAMPLE_SECTION_PATTERN.finditer(javadoc):
            code = match.group(1).strip()
            code = cls._clean_code_block(code)
            if code:
                examples.append({'code': code, 'description': 'Usage example'})
        
        return examples
    
    @classmethod
    def _clean_code_block(cls, code: str) -> str:
        """Clean up a code block from JavaDoc."""
        # Remove JavaDoc asterisks at start of lines
        code = re.sub(r'^\s*\*\s?', '', code, flags=re.MULTILINE)
        # Remove HTML entities
        code = code.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        # Remove excessive blank lines
        code = re.sub(r'\n{3,}', '\n\n', code)
        # Trim
        code = code.strip()
        # Limit length
        if len(code) > 500:
            code = code[:497] + "..."
        return code
    
    @classmethod
    def parse_deprecation(cls, javadoc: Optional[str], 
                          annotations: List[str] = None) -> Optional[DeprecationInfo]:
        """Extract deprecation information from JavaDoc and annotations."""
        if not javadoc and not annotations:
            return None
        
        annotations = annotations or []
        is_deprecated = 'Deprecated' in annotations
        
        deprecated_match = cls.DEPRECATED_TAG_PATTERN.search(javadoc or '')
        if deprecated_match:
            is_deprecated = True
        
        if not is_deprecated:
            return None
        
        deprecation = DeprecationInfo(deprecated=True)
        
        if deprecated_match:
            deprecated_text = deprecated_match.group(1)
            deprecated_text = cls._clean_javadoc_text(deprecated_text)
            deprecation.reason = deprecated_text
            
            replacement = cls._extract_replacement(deprecated_text)
            if replacement:
                deprecation.replacement = replacement
            
            if re.search(r'(?:for\s+removal|will\s+be\s+removed|to\s+be\s+removed)', 
                        deprecated_text, re.IGNORECASE):
                deprecation.for_removal = True
            
            since_match = re.search(r'since\s+([\d.]+)', deprecated_text, re.IGNORECASE)
            if since_match:
                deprecation.since = since_match.group(1)
        
        # If no replacement found in @deprecated, check @see tags
        if not deprecation.replacement and javadoc:
            see_matches = cls.SEE_PATTERN.findall(javadoc)
            if see_matches:
                # Use first @see as potential replacement
                for match in see_matches:
                    ref = match[0] or match[1]
                    if ref:
                        deprecation.replacement = cls._clean_javadoc_text(ref)
                        break
        
        return deprecation
    
    @classmethod
    def _extract_replacement(cls, text: str) -> str:
        """Extract replacement method/class from deprecation text."""
        if not text:
            return ""
        
        links = cls.LINK_PATTERN.findall(text)
        
        for pattern in cls.REPLACEMENT_PATTERNS:
            match = pattern.search(text)
            if match:
                replacement = match.group(1).strip()
                replacement = re.sub(r'[.,;:!?\s]+$', '', replacement)
                replacement = replacement.strip('{}')
                if replacement and not replacement.lower() in ('a', 'the', 'an', 'this', 'that'):
                    return replacement
        
        if links:
            return links[0].strip()
        
        code_refs = cls.CODE_PATTERN.findall(text)
        if code_refs:
            for ref in code_refs:
                if re.match(r'^[A-Z]\w*(?:#\w+)?$|^\w+\(\)$', ref):
                    return ref
        
        return ""
    
    @classmethod
    def _clean_javadoc_text(cls, text: str) -> str:
        """Clean up JavaDoc text by removing formatting artifacts."""
        if not text:
            return ""
        
        text = re.sub(r'\n\s*\*\s*', ' ', text)
        text = cls.LINK_PATTERN.sub(r'\1', text)
        text = cls.CODE_PATTERN.sub(r'\1', text)
        text = re.sub(r'\{@\w+\s+([^}]*)\}', r'\1', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        if len(text) > 300:
            text = text[:297] + "..."
        
        return text


class JavaParser:
    """Parser for extracting API information from Java source files."""
    
    PACKAGE_PATTERN = re.compile(r'^\s*package\s+([\w.]+)\s*;', re.MULTILINE)
    
    IMPORT_PATTERN = re.compile(r'^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;', re.MULTILINE)
    
    CLASS_PATTERN = re.compile(
        r'(?P<javadoc>/\*\*[\s\S]*?\*/\s*)?'
        r'(?P<annotations>(?:@\w+(?:\([^)]*\))?\s*)*)'
        r'(?P<modifiers>(?:public|private|protected|static|final|abstract|strictfp)\s+)*'
        r'(?P<type>class|interface|enum|record)\s+'
        r'(?P<name>\w+)'
        r'(?:<[^>]+>)?'
        r'(?:\s+extends\s+(?P<extends>[\w.<>,\s]+?))?'
        r'(?:\s+implements\s+(?P<implements>[\w.<>,\s]+?))?'
        r'\s*\{',
        re.MULTILINE
    )
    
    METHOD_PATTERN = re.compile(
        r'(?P<javadoc>/\*\*[\s\S]*?\*/\s*)?'
        r'(?P<annotations>(?:@\w+(?:\([^)]*\))?\s*)*)'
        r'(?P<modifiers>(?:(?:public|private|protected|static|final|abstract|synchronized|native|default)\s+)*)'
        r'(?P<generics><[^>]+>\s+)?'
        r'(?P<return>[\w.<>,\[\]\s\?]+?)\s+'
        r'(?P<name>\w+)\s*'
        r'\((?P<params>[^)]*)\)\s*'
        r'(?:throws\s+(?P<throws>[\w,\s]+))?'
        r'\s*[{;]',
        re.MULTILINE
    )
    
    FIELD_PATTERN = re.compile(
        r'(?P<javadoc>/\*\*[\s\S]*?\*/\s*)?'
        r'(?P<annotations>(?:@\w+(?:\([^)]*\))?\s*)*)'
        r'(?P<modifiers>(?:(?:public|private|protected|static|final|volatile|transient)\s+)+)'
        r'(?P<type>[\w.<>,\[\]\?]+)\s+'
        r'(?P<name>\w+)\s*'
        r'(?:=\s*[^;]+)?;',
        re.MULTILINE
    )
    
    DEPRECATED_ANNOTATION_PATTERN = re.compile(
        r'@Deprecated(?:\s*\(\s*'
        r'(?:since\s*=\s*["\']([^"\']+)["\'])?'
        r'(?:\s*,\s*)?'
        r'(?:forRemoval\s*=\s*(true|false))?'
        r'\s*\))?',
        re.IGNORECASE
    )
    
    ANNOTATION_PATTERN = re.compile(r'@(\w+)(?:\([^)]*\))?')
    
    def __init__(self, include_private: bool = False, include_protected: bool = True,
                 max_fields: int = 500, max_methods: int = 500):
        self.include_private = include_private
        self.include_protected = include_protected
        self.max_fields = max_fields
        self.max_methods = max_methods
        self.javadoc_parser = JavaDocParser()
        
    def parse_file(self, file_path: str, verbose: bool = False) -> Optional[JavaClass]:
        """Parse a Java file and extract API information."""
        thread_id = id(file_path)
        
        try:
            with current_files_lock:
                current_files[thread_id] = file_path
            
            if verbose:
                print(f"  Processing: {file_path}")
            
            file_size = os.path.getsize(file_path)
            
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            static_final_count = len(re.findall(r'static\s+final', content))
            if static_final_count > 1000:
                if verbose:
                    print(f"    Warning: {file_path} has {static_final_count} static final fields, using simplified parsing")
                return self._parse_large_constant_file(file_path, content)
            
            result = self.parse_content(content)
            return result
            
        except Exception as e:
            if verbose:
                print(f"  Error: {file_path}: {e}")
                traceback.print_exc()
            raise
        finally:
            with current_files_lock:
                current_files.pop(thread_id, None)
    
    def _parse_large_constant_file(self, file_path: str, content: str) -> Optional[JavaClass]:
        """Simplified parsing for large constant files."""
        package_match = self.PACKAGE_PATTERN.search(content)
        package = package_match.group(1) if package_match else ""
        
        class_match = self.CLASS_PATTERN.search(content)
        if not class_match:
            return None
        
        java_class = JavaClass(
            name=class_match.group('name'),
            package=package,
            class_type=class_match.group('type'),
            extends=self._clean_type(class_match.group('extends')) if class_match.group('extends') else None,
            implements=self._parse_implements(class_match.group('implements')),
            description=f"Large constant file with many static fields (simplified parsing)",
            annotations=self._extract_annotations(class_match.group('annotations')),
            modifiers=self._parse_modifiers(class_match.group('modifiers'))
        )
        
        field_count = len(re.findall(self.FIELD_PATTERN, content))
        if field_count > 0:
            java_class.fields = [Field(
                name="_CONSTANT_COUNT_",
                type="int",
                description=f"This file contains approximately {field_count} constant fields",
                modifiers=['static', 'final']
            )]
        
        return java_class
    
    def parse_content(self, content: str) -> Optional[JavaClass]:
        """Parse Java source content and extract API information."""
        content_no_comments = re.sub(r'(?<!/)//.*$', '', content, flags=re.MULTILINE)
        
        package_match = self.PACKAGE_PATTERN.search(content_no_comments)
        package = package_match.group(1) if package_match else ""
        
        class_match = self.CLASS_PATTERN.search(content_no_comments)
        if not class_match:
            return None
        
        class_javadoc = class_match.group('javadoc')
        class_javadoc_info = JavaDocParser.parse_javadoc(class_javadoc)
        class_annotations = self._extract_annotations(class_match.group('annotations'))
        
        class_deprecation = self._parse_full_deprecation(
            class_javadoc, 
            class_match.group('annotations'),
            class_annotations
        )
        
        java_class = JavaClass(
            name=class_match.group('name'),
            package=package,
            class_type=class_match.group('type'),
            extends=self._clean_type(class_match.group('extends')) if class_match.group('extends') else None,
            implements=self._parse_implements(class_match.group('implements')),
            description=class_javadoc_info['description'],
            annotations=class_annotations,
            modifiers=self._parse_modifiers(class_match.group('modifiers')),
            deprecation=class_deprecation,
            see_also=class_javadoc_info.get('see', []),
            examples=[UsageExample(**ex) for ex in class_javadoc_info.get('examples', [])]
        )
        
        class_body = self._extract_class_body(content_no_comments, class_match.end() - 1)
        
        if java_class.class_type == 'enum':
            java_class.enum_values = self._extract_enum_values(class_body)
        
        java_class.fields = self._extract_fields(class_body)
        
        methods, constructors = self._extract_methods(class_body, java_class.name)
        java_class.methods = methods
        java_class.constructors = constructors
        
        java_class = self._process_lombok_annotations(java_class)
        
        return java_class
    
    def _parse_full_deprecation(self, javadoc: Optional[str], 
                                 annotations_str: Optional[str],
                                 annotations: List[str]) -> Optional[DeprecationInfo]:
        """Parse deprecation info from both JavaDoc and @Deprecated annotation."""
        deprecation = JavaDocParser.parse_deprecation(javadoc, annotations)
        
        if not deprecation and 'Deprecated' not in annotations:
            return None
        
        if not deprecation:
            deprecation = DeprecationInfo(deprecated=True)
        
        if annotations_str:
            dep_match = self.DEPRECATED_ANNOTATION_PATTERN.search(annotations_str)
            if dep_match:
                if dep_match.group(1):
                    deprecation.since = dep_match.group(1)
                if dep_match.group(2):
                    deprecation.for_removal = dep_match.group(2).lower() == 'true'
        
        return deprecation
    
    def _extract_class_body(self, content: str, start_pos: int) -> str:
        """Extract the body of a class between braces."""
        brace_count = 0
        in_class = False
        end_pos = start_pos
        
        for i in range(start_pos, len(content)):
            char = content[i]
            if char == '{':
                brace_count += 1
                in_class = True
            elif char == '}':
                brace_count -= 1
                if in_class and brace_count == 0:
                    end_pos = i
                    break
        
        return content[start_pos:end_pos + 1]
    
    def _extract_annotations(self, annotations_str: Optional[str]) -> List[str]:
        """Extract annotation names from annotation string."""
        if not annotations_str:
            return []
        return self.ANNOTATION_PATTERN.findall(annotations_str)
    
    def _parse_modifiers(self, modifiers_str: Optional[str]) -> List[str]:
        """Parse modifier string into list."""
        if not modifiers_str:
            return []
        return modifiers_str.split()
    
    def _parse_implements(self, implements_str: Optional[str]) -> List[str]:
        """Parse implements clause into list of interfaces."""
        if not implements_str:
            return []
        interfaces = [self._clean_type(i.strip()) for i in implements_str.split(',')]
        return [i for i in interfaces if i]
    
    def _clean_type(self, type_str: Optional[str]) -> str:
        """Clean up a type string."""
        if not type_str:
            return ""
        return re.sub(r'\s+', ' ', type_str.strip())
    
    def _extract_enum_values(self, class_body: str) -> List[str]:
        """Extract enum constant names."""
        match = re.search(r'\{[\s]*([^;{]+?)(?:;|\}|(?=\s+\w+\s*\())', class_body)
        if match:
            values_str = match.group(1)
            values = re.findall(r'\b([A-Z_][A-Z0-9_]*)\b', values_str)
            return values[:100]
        return []
    
    def _extract_fields(self, class_body: str) -> List[Field]:
        """Extract field declarations."""
        fields = []
        field_count = 0
        
        for match in self.FIELD_PATTERN.finditer(class_body):
            field_count += 1
            if field_count > self.max_fields:
                fields.append(Field(
                    name="_TRUNCATED_",
                    type="...",
                    description=f"Field extraction truncated. Total fields exceed {self.max_fields}",
                    modifiers=[]
                ))
                break
            
            modifiers = self._parse_modifiers(match.group('modifiers'))
            
            if not self._should_include(modifiers):
                continue
            
            annotations = self._extract_annotations(match.group('annotations'))
            javadoc = match.group('javadoc')
            javadoc_info = JavaDocParser.parse_javadoc(javadoc)
            
            deprecation = self._parse_full_deprecation(
                javadoc,
                match.group('annotations'),
                annotations
            )
            
            field = Field(
                name=match.group('name'),
                type=self._clean_type(match.group('type')),
                modifiers=modifiers,
                annotations=annotations,
                description=javadoc_info['description'],
                deprecation=deprecation,
                see_also=javadoc_info.get('see', [])
            )
            fields.append(field)
        
        return fields
    
    def _extract_methods(self, class_body: str, class_name: str) -> Tuple[List[Method], List[Method]]:
        """Extract method and constructor declarations."""
        methods = []
        constructors = []
        method_count = 0
        
        for match in self.METHOD_PATTERN.finditer(class_body):
            method_count += 1
            if method_count > self.max_methods:
                methods.append(Method(
                    name="_TRUNCATED_",
                    returns="...",
                    description=f"Method extraction truncated. Total methods exceed {self.max_methods}",
                    modifiers=[]
                ))
                break
            
            name = match.group('name')
            modifiers = self._parse_modifiers(match.group('modifiers'))
            return_type = self._clean_type(match.group('return'))
            
            if modifiers and not self._should_include(modifiers):
                continue
            
            annotations = self._extract_annotations(match.group('annotations'))
            javadoc = match.group('javadoc')
            javadoc_info = JavaDocParser.parse_javadoc(javadoc)
            
            deprecation = self._parse_full_deprecation(
                javadoc,
                match.group('annotations'),
                annotations
            )
            
            params = self._parse_parameters(match.group('params'), javadoc_info['params'])
            
            throws = []
            if match.group('throws'):
                throws = [t.strip() for t in match.group('throws').split(',')]
            
            method = Method(
                name=name,
                returns=return_type,
                params=params,
                modifiers=modifiers,
                annotations=annotations,
                description=javadoc_info['description'],
                deprecation=deprecation,
                throws=throws,
                return_description=javadoc_info['returns'],
                see_also=javadoc_info.get('see', []),
                examples=[UsageExample(**ex) for ex in javadoc_info.get('examples', [])]
            )
            
            if name == class_name:
                method.returns = ""
                constructors.append(method)
            else:
                methods.append(method)
        
        return methods, constructors
    
    def _parse_parameters(self, params_str: str, 
                          param_docs: Dict[str, str]) -> List[Parameter]:
        """Parse parameter string into list of Parameter objects."""
        if not params_str or not params_str.strip():
            return []
        
        params = []
        temp_str = params_str
        generics = re.findall(r'<[^>]+>', temp_str)
        for i, g in enumerate(generics):
            temp_str = temp_str.replace(g, f'__GENERIC_{i}__')
        
        parts = temp_str.split(',')
        
        for i, part in enumerate(parts):
            for j, g in enumerate(generics):
                part = part.replace(f'__GENERIC_{j}__', g)
            
            part = part.strip()
            if not part:
                continue
            
            part = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', part)
            part = re.sub(r'\bfinal\s+', '', part)
            
            tokens = part.split()
            if len(tokens) >= 2:
                param_name = tokens[-1]
                param_type = ' '.join(tokens[:-1])
                if '...' in param_type:
                    param_type = param_type.replace('...', '[]')
                    param_name = param_name + ' (varargs)'
                
                clean_name = param_name.replace(' (varargs)', '')
                description = param_docs.get(clean_name, '')
                
                params.append(Parameter(
                    name=param_name, 
                    type=param_type,
                    description=description
                ))
            elif len(tokens) == 1:
                params.append(Parameter(name="", type=tokens[0]))
        
        return params
    
    def _should_include(self, modifiers: List[str]) -> bool:
        """Check if a member should be included based on visibility."""
        if 'private' in modifiers:
            return self.include_private
        if 'protected' in modifiers:
            return self.include_protected
        return True
    
    def _process_lombok_annotations(self, java_class: JavaClass) -> JavaClass:
        """Add synthetic methods for Lombok annotations."""
        class_annotations = java_class.annotations
        
        class_has_getter = 'Getter' in class_annotations
        class_has_setter = 'Setter' in class_annotations
        class_has_data = 'Data' in class_annotations
        class_has_builder = 'Builder' in class_annotations
        
        for field in java_class.fields:
            field_annotations = field.annotations
            has_getter = class_has_getter or class_has_data or 'Getter' in field_annotations
            has_setter = class_has_setter or class_has_data or 'Setter' in field_annotations
            
            if has_getter and 'static' not in field.modifiers:
                getter_name = self._get_getter_name(field.name, field.type)
                if not any(m.name == getter_name for m in java_class.methods):
                    java_class.methods.append(Method(
                        name=getter_name,
                        returns=field.type,
                        params=[],
                        description=f"Gets {field.name} (Lombok generated)",
                        modifiers=['public'],
                        annotations=['Generated']
                    ))
            
            if has_setter and 'static' not in field.modifiers and 'final' not in field.modifiers:
                setter_name = f"set{field.name[0].upper()}{field.name[1:]}"
                if not any(m.name == setter_name for m in java_class.methods):
                    java_class.methods.append(Method(
                        name=setter_name,
                        returns='void',
                        params=[Parameter(name=field.name, type=field.type)],
                        description=f"Sets {field.name} (Lombok generated)",
                        modifiers=['public'],
                        annotations=['Generated']
                    ))
        
        if class_has_builder:
            java_class.methods.append(Method(
                name='builder',
                returns=f'{java_class.name}Builder',
                params=[],
                description='Creates a builder for this class (Lombok generated)',
                modifiers=['public', 'static'],
                annotations=['Generated']
            ))
        
        return java_class
    
    def _get_getter_name(self, field_name: str, field_type: str) -> str:
        """Generate getter name based on field name and type."""
        prefix = 'is' if field_type == 'boolean' else 'get'
        return f"{prefix}{field_name[0].upper()}{field_name[1:]}"


class APIDocumentationGenerator:
    """Generates compact API documentation from Java source files."""
    
    def __init__(self, 
                 include_private: bool = False,
                 include_protected: bool = True,
                 compact_mode: bool = True,
                 include_deprecated_info: bool = True,
                 include_examples: bool = True,
                 include_see_also: bool = True,
                 max_workers: int = 4,
                 timeout: int = 30,
                 max_file_size: int = 0,
                 verbose: bool = False):
        self.parser = JavaParser(include_private, include_protected)
        self.compact_mode = compact_mode
        self.include_deprecated_info = include_deprecated_info
        self.include_examples = include_examples
        self.include_see_also = include_see_also
        self.max_workers = max_workers
        self.timeout = timeout
        self.max_file_size = max_file_size
        self.verbose = verbose
    
    def process_directory(self, directory: str, output_file: str, 
                         exclude_patterns: List[str] = None) -> Dict[str, Any]:
        """Process all Java files in a directory recursively."""
        exclude_patterns = exclude_patterns or []
        java_files = self._find_java_files(directory, exclude_patterns)
        
        print(f"Found {len(java_files)} Java files to process...")
        
        results = []
        errors = []
        timeouts = []
        skipped = []
        deprecated_count = 0
        
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_file = {}
            
            for f in java_files:
                if self.max_file_size > 0:
                    try:
                        file_size = os.path.getsize(f)
                        if file_size > self.max_file_size:
                            skipped.append({
                                'file': f, 
                                'reason': f'File size {file_size / 1024:.1f}KB exceeds limit {self.max_file_size / 1024:.1f}KB'
                            })
                            if self.verbose:
                                print(f"  Skipping (too large): {f}")
                            continue
                    except OSError:
                        pass
                
                future = executor.submit(self.parser.parse_file, f, self.verbose)
                future_to_file[future] = f
            
            completed = 0
            total = len(future_to_file)
            
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                completed += 1
                
                try:
                    java_class = future.result(timeout=self.timeout)
                    if java_class:
                        results.append(java_class)
                        if java_class.deprecation:
                            deprecated_count += 1
                        deprecated_count += sum(1 for m in java_class.methods if m.deprecation)
                        deprecated_count += sum(1 for f in java_class.fields if f.deprecation)
                        
                except FuturesTimeoutError:
                    timeouts.append({'file': file_path, 'timeout': self.timeout})
                    print(f"  TIMEOUT ({self.timeout}s): {file_path}")
                    
                except Exception as e:
                    errors.append({'file': file_path, 'error': str(e)})
                    if self.verbose:
                        print(f"  ERROR: {file_path}: {e}")
                
                if completed % 100 == 0 or completed == total:
                    elapsed = time.time() - start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    print(f"Progress: {completed}/{total} files ({rate:.1f} files/sec)")
        
        elapsed_total = time.time() - start_time
        print(f"\nCompleted in {elapsed_total:.1f} seconds")
        print(f"Successfully parsed {len(results)} classes")
        print(f"Found {deprecated_count} deprecated items")
        
        if timeouts:
            print(f"Timeouts: {len(timeouts)}")
        if skipped:
            print(f"Skipped (size limit): {len(skipped)}")
        
        output = self._generate_output(results)
        self._write_output(output, output_file)
        
        return {
            'total_files': len(java_files),
            'successful': len(results),
            'errors': len(errors),
            'timeouts': len(timeouts),
            'skipped': len(skipped),
            'deprecated_items': deprecated_count,
            'error_details': errors[:10],
            'timeout_details': timeouts[:10],
            'skipped_details': skipped[:10],
            'elapsed_seconds': elapsed_total
        }
    
    def _find_java_files(self, directory: str, exclude_patterns: List[str]) -> List[str]:
        """Find all Java files in directory, excluding patterns."""
        java_files = []
        
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not any(
                re.search(pattern, os.path.join(root, d)) 
                for pattern in exclude_patterns
            )]
            
            for file in files:
                if file.endswith('.java'):
                    file_path = os.path.join(root, file)
                    if not any(re.search(pattern, file_path) for pattern in exclude_patterns):
                        java_files.append(file_path)
        
        return java_files
    
    def _generate_output(self, classes: List[JavaClass]) -> List[Dict[str, Any]]:
        """Generate the output documentation format."""
        output = []
        
        for java_class in classes:
            if self.compact_mode:
                doc = self._to_compact_dict(java_class)
            else:
                doc = self._to_full_dict(java_class)
            output.append(doc)
        
        output.sort(key=lambda x: (x.get('pkg', x.get('package', '')), x.get('name', '')))
        
        return output
    
    def _to_compact_dict(self, java_class: JavaClass) -> Dict[str, Any]:
        """Convert JavaClass to compact dictionary format."""
        doc = {
            'name': java_class.name,
            'pkg': java_class.package,
            'type': java_class.class_type[0]
        }
        
        if java_class.extends:
            doc['ext'] = java_class.extends
        
        if java_class.implements:
            doc['impl'] = java_class.implements
        
        if java_class.description:
            doc['desc'] = java_class.description
        
        if java_class.annotations:
            notable = [a for a in java_class.annotations 
                      if a in ('Deprecated', 'FunctionalInterface', 'Getter', 'Setter', 
                              'Data', 'Builder', 'Slf4j', 'Service', 'Component')]
            if notable:
                doc['ann'] = notable
        
        if java_class.deprecation and self.include_deprecated_info:
            doc['dep'] = self._compact_deprecation(java_class.deprecation)
        
        if java_class.see_also and self.include_see_also:
            doc['see'] = java_class.see_also
        
        if java_class.examples and self.include_examples:
            doc['ex'] = [self._compact_example(ex) for ex in java_class.examples]
        
        if java_class.enum_values:
            doc['vals'] = java_class.enum_values
        
        if java_class.fields:
            doc['flds'] = [self._compact_field(f) for f in java_class.fields]
        
        if java_class.constructors:
            doc['ctors'] = [self._compact_method(m) for m in java_class.constructors]
        
        if java_class.methods:
            doc['mtds'] = [self._compact_method(m) for m in java_class.methods]
        
        return doc
    
    def _compact_deprecation(self, dep: DeprecationInfo) -> Dict[str, Any]:
        """Convert deprecation info to compact format."""
        result = {}
        
        if dep.replacement:
            result['use'] = dep.replacement
        
        if dep.since:
            result['since'] = dep.since
        
        if dep.for_removal:
            result['rm'] = True
        
        if dep.reason and not dep.replacement:
            result['why'] = dep.reason[:100] if len(dep.reason) > 100 else dep.reason
        
        return result if result else {'dep': True}
    
    def _compact_example(self, ex: UsageExample) -> Dict[str, Any]:
        """Convert example to compact format."""
        result = {'code': ex.code}
        if ex.description:
            result['desc'] = ex.description
        return result
    
    def _compact_field(self, field: Field) -> Dict[str, Any]:
        """Convert field to compact format."""
        f = {'n': field.name, 't': field.type}
        
        if 'static' in field.modifiers:
            f['s'] = True
        if 'final' in field.modifiers:
            f['f'] = True
        if field.description:
            f['d'] = field.description
        
        if field.deprecation and self.include_deprecated_info:
            f['dep'] = self._compact_deprecation(field.deprecation)
        
        if field.see_also and self.include_see_also:
            f['see'] = field.see_also
        
        return f
    
    def _compact_method(self, method: Method) -> Dict[str, Any]:
        """Convert method to compact format."""
        m = {'n': method.name}
        
        if method.returns:
            m['r'] = method.returns
        
        if method.params:
            m['p'] = []
            for p in method.params:
                if p.name:
                    param_dict = {'n': p.name, 't': p.type}
                    if p.description:
                        param_dict['d'] = p.description
                    m['p'].append(param_dict)
                else:
                    m['p'].append(p.type)
        
        if 'static' in method.modifiers:
            m['s'] = True
        
        if method.description:
            m['d'] = method.description
        
        if method.return_description:
            m['rd'] = method.return_description
        
        if method.throws:
            m['throws'] = method.throws
        
        if method.deprecation and self.include_deprecated_info:
            m['dep'] = self._compact_deprecation(method.deprecation)
        
        if method.see_also and self.include_see_also:
            m['see'] = method.see_also
        
        if method.examples and self.include_examples:
            m['ex'] = [self._compact_example(ex) for ex in method.examples]
        
        return m
    
    def _to_full_dict(self, java_class: JavaClass) -> Dict[str, Any]:
        """Convert JavaClass to full dictionary format."""
        doc = {
            'name': java_class.name,
            'package': java_class.package,
            'type': java_class.class_type,
            'modifiers': java_class.modifiers,
            'annotations': java_class.annotations
        }
        
        if java_class.extends:
            doc['extends'] = java_class.extends
        
        if java_class.implements:
            doc['implements'] = java_class.implements
        
        if java_class.description:
            doc['description'] = java_class.description
        
        if java_class.deprecation:
            doc['deprecation'] = self._full_deprecation(java_class.deprecation)
        
        if java_class.see_also:
            doc['seeAlso'] = java_class.see_also
        
        if java_class.examples:
            doc['examples'] = [{'code': ex.code, 'description': ex.description} for ex in java_class.examples]
        
        if java_class.enum_values:
            doc['enumValues'] = java_class.enum_values
        
        if java_class.fields:
            doc['fields'] = [self._full_field(f) for f in java_class.fields]
        
        if java_class.constructors:
            doc['constructors'] = [self._full_method(m) for m in java_class.constructors]
        
        if java_class.methods:
            doc['methods'] = [self._full_method(m) for m in java_class.methods]
        
        return doc
    
    def _full_deprecation(self, dep: DeprecationInfo) -> Dict[str, Any]:
        """Convert deprecation info to full format."""
        return {
            'deprecated': dep.deprecated,
            'replacement': dep.replacement,
            'since': dep.since,
            'forRemoval': dep.for_removal,
            'reason': dep.reason
        }
    
    def _full_field(self, field: Field) -> Dict[str, Any]:
        """Convert field to full format."""
        result = {
            'name': field.name,
            'type': field.type,
            'modifiers': field.modifiers,
            'annotations': field.annotations,
            'description': field.description
        }
        if field.deprecation:
            result['deprecation'] = self._full_deprecation(field.deprecation)
        if field.see_also:
            result['seeAlso'] = field.see_also
        return result
    
    def _full_method(self, method: Method) -> Dict[str, Any]:
        """Convert method to full format."""
        result = {
            'name': method.name,
            'returns': method.returns,
            'returnDescription': method.return_description,
            'params': [
                {
                    'name': p.name,
                    'type': p.type,
                    'description': p.description
                } for p in method.params
            ],
            'modifiers': method.modifiers,
            'annotations': method.annotations,
            'description': method.description,
            'throws': method.throws
        }
        if method.deprecation:
            result['deprecation'] = self._full_deprecation(method.deprecation)
        if method.see_also:
            result['seeAlso'] = method.see_also
        if method.examples:
            result['examples'] = [{'code': ex.code, 'description': ex.description} for ex in method.examples]
        return result
    
    def _write_output(self, output: List[Dict[str, Any]], output_file: str):
        """Write output to file."""
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=None if self.compact_mode else 2, 
                     separators=(',', ':') if self.compact_mode else None,
                     ensure_ascii=False)
        
        file_size = os.path.getsize(output_file)
        print(f"Output written to {output_file} ({file_size / 1024:.1f} KB)")


def create_index_file(api_docs: List[Dict], output_file: str):
    """Create a separate index file for quick lookups."""
    index = {
        'packages': {},
        'classes': {},
        'methods': {},
        'deprecated': {
            'classes': [],
            'methods': [],
            'fields': []
        }
    }
    
    for doc in api_docs:
        pkg = doc.get('pkg', doc.get('package', ''))
        name = doc.get('name', '')
        full_name = f"{pkg}.{name}" if pkg else name
        
        if pkg not in index['packages']:
            index['packages'][pkg] = []
        index['packages'][pkg].append(name)
        
        index['classes'][name] = {
            'pkg': pkg,
            'type': doc.get('type', 'c'),
            'ext': doc.get('ext', doc.get('extends')),
            'impl': doc.get('impl', doc.get('implements', []))
        }
        
        if 'dep' in doc or 'deprecation' in doc:
            dep_info = doc.get('dep', doc.get('deprecation', {}))
            index['deprecated']['classes'].append({
                'name': full_name,
                'replacement': dep_info.get('use', dep_info.get('replacement', '')),
                'see': doc.get('see', doc.get('seeAlso', []))
            })
        
        methods = doc.get('mtds', doc.get('methods', []))
        for m in methods:
            method_name = m.get('n', m.get('name', ''))
            if method_name not in index['methods']:
                index['methods'][method_name] = []
            index['methods'][method_name].append(full_name)
            
            if 'dep' in m or 'deprecation' in m:
                dep_info = m.get('dep', m.get('deprecation', {}))
                index['deprecated']['methods'].append({
                    'name': f"{full_name}#{method_name}",
                    'replacement': dep_info.get('use', dep_info.get('replacement', '')),
                    'see': m.get('see', m.get('seeAlso', []))
                })
        
        fields = doc.get('flds', doc.get('fields', []))
        for f in fields:
            if 'dep' in f or 'deprecation' in f:
                field_name = f.get('n', f.get('name', ''))
                dep_info = f.get('dep', f.get('deprecation', {}))
                index['deprecated']['fields'].append({
                    'name': f"{full_name}#{field_name}",
                    'replacement': dep_info.get('use', dep_info.get('replacement', '')),
                    'see': f.get('see', f.get('seeAlso', []))
                })
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(index, f, separators=(',', ':'), ensure_ascii=False)
    
    file_size = os.path.getsize(output_file)
    print(f"Index written to {output_file} ({file_size / 1024:.1f} KB)")
    
    dep = index['deprecated']
    total_deprecated = len(dep['classes']) + len(dep['methods']) + len(dep['fields'])
    if total_deprecated > 0:
        print(f"\nDeprecation Summary:")
        print(f"  - Deprecated classes: {len(dep['classes'])}")
        print(f"  - Deprecated methods: {len(dep['methods'])}")
        print(f"  - Deprecated fields: {len(dep['fields'])}")


def create_deprecation_report(api_docs: List[Dict], output_file: str):
    """Create a focused report of all deprecated items and their replacements."""
    report = {
        'summary': {
            'total_deprecated': 0,
            'with_replacement': 0,
            'for_removal': 0
        },
        'items': []
    }
    
    for doc in api_docs:
        pkg = doc.get('pkg', doc.get('package', ''))
        class_name = doc.get('name', '')
        full_name = f"{pkg}.{class_name}" if pkg else class_name
        
        class_dep = doc.get('dep', doc.get('deprecation'))
        if class_dep:
            item = {
                'type': 'class',
                'name': full_name,
                'replacement': class_dep.get('use', class_dep.get('replacement', '')),
                'since': class_dep.get('since', ''),
                'forRemoval': class_dep.get('rm', class_dep.get('forRemoval', False)),
                'reason': class_dep.get('why', class_dep.get('reason', '')),
                'seeAlso': doc.get('see', doc.get('seeAlso', []))
            }
            report['items'].append(item)
            report['summary']['total_deprecated'] += 1
            if item['replacement']:
                report['summary']['with_replacement'] += 1
            if item['forRemoval']:
                report['summary']['for_removal'] += 1
        
        methods = doc.get('mtds', doc.get('methods', []))
        for m in methods:
            method_dep = m.get('dep', m.get('deprecation'))
            if method_dep:
                method_name = m.get('n', m.get('name', ''))
                params = m.get('p', m.get('params', []))
                param_types = []
                for p in params:
                    if isinstance(p, dict):
                        param_types.append(p.get('t', p.get('type', '')))
                    else:
                        param_types.append(str(p))
                signature = f"{method_name}({', '.join(param_types)})"
                
                item = {
                    'type': 'method',
                    'name': f"{full_name}#{signature}",
                    'replacement': method_dep.get('use', method_dep.get('replacement', '')),
                    'since': method_dep.get('since', ''),
                    'forRemoval': method_dep.get('rm', method_dep.get('forRemoval', False)),
                    'reason': method_dep.get('why', method_dep.get('reason', '')),
                    'seeAlso': m.get('see', m.get('seeAlso', []))
                }
                report['items'].append(item)
                report['summary']['total_deprecated'] += 1
                if item['replacement']:
                    report['summary']['with_replacement'] += 1
                if item['forRemoval']:
                    report['summary']['for_removal'] += 1
        
        fields = doc.get('flds', doc.get('fields', []))
        for f in fields:
            field_dep = f.get('dep', f.get('deprecation'))
            if field_dep:
                field_name = f.get('n', f.get('name', ''))
                item = {
                    'type': 'field',
                    'name': f"{full_name}#{field_name}",
                    'replacement': field_dep.get('use', field_dep.get('replacement', '')),
                    'since': field_dep.get('since', ''),
                    'forRemoval': field_dep.get('rm', field_dep.get('forRemoval', False)),
                    'reason': field_dep.get('why', field_dep.get('reason', '')),
                    'seeAlso': f.get('see', f.get('seeAlso', []))
                }
                report['items'].append(item)
                report['summary']['total_deprecated'] += 1
                if item['replacement']:
                    report['summary']['with_replacement'] += 1
                if item['forRemoval']:
                    report['summary']['for_removal'] += 1
    
    report['items'].sort(key=lambda x: (x['type'], x['name']))
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    file_size = os.path.getsize(output_file)
    print(f"\nDeprecation report written to {output_file} ({file_size / 1024:.1f} KB)")
    print(f"  Total deprecated: {report['summary']['total_deprecated']}")
    print(f"  With replacement: {report['summary']['with_replacement']}")
    print(f"  Marked for removal: {report['summary']['for_removal']}")


def create_cross_reference(api_docs: List[Dict], output_file: str):
    """Create a cross-reference index showing type relationships."""
    xref = {
        'returns_type': {},      # What methods return this type
        'accepts_type': {},      # What methods accept this type as parameter
        'extends_class': {},     # What classes extend this class
        'implements_interface': {},  # What classes implement this interface
        'related': {}            # Classes mentioned in @see tags
    }
    
    for doc in api_docs:
        pkg = doc.get('pkg', doc.get('package', ''))
        name = doc.get('name', '')
        full_name = f"{pkg}.{name}" if pkg else name
        
        # Track inheritance
        ext = doc.get('ext', doc.get('extends'))
        if ext:
            base_name = ext.split('<')[0].split('.')[-1]
            if base_name not in xref['extends_class']:
                xref['extends_class'][base_name] = []
            xref['extends_class'][base_name].append(full_name)
        
        # Track interface implementation
        for impl in doc.get('impl', doc.get('implements', [])):
            iface_name = impl.split('<')[0].split('.')[-1]
            if iface_name not in xref['implements_interface']:
                xref['implements_interface'][iface_name] = []
            xref['implements_interface'][iface_name].append(full_name)
        
        # Track @see references
        for see in doc.get('see', doc.get('seeAlso', [])):
            see_name = see.split('#')[0].split('.')[-1]
            if see_name and see_name != name:
                if see_name not in xref['related']:
                    xref['related'][see_name] = []
                if full_name not in xref['related'][see_name]:
                    xref['related'][see_name].append(full_name)
        
        # Track method return types and parameters
        for m in doc.get('mtds', doc.get('methods', [])):
            method_name = m.get('n', m.get('name', ''))
            method_full = f"{full_name}#{method_name}"
            
            # Return type
            ret = m.get('r', m.get('returns', ''))
            if ret:
                ret_name = ret.split('<')[0].split('.')[-1]
                if ret_name and ret_name not in ('void', 'int', 'long', 'boolean', 'double', 'float', 'String'):
                    if ret_name not in xref['returns_type']:
                        xref['returns_type'][ret_name] = []
                    xref['returns_type'][ret_name].append(method_full)
            
            # Parameter types
            for p in m.get('p', m.get('params', [])):
                if isinstance(p, dict):
                    ptype = p.get('t', p.get('type', ''))
                else:
                    ptype = str(p)
                ptype_name = ptype.split('<')[0].split('.')[-1]
                if ptype_name and ptype_name not in ('int', 'long', 'boolean', 'double', 'float', 'String'):
                    if ptype_name not in xref['accepts_type']:
                        xref['accepts_type'][ptype_name] = []
                    xref['accepts_type'][ptype_name].append(method_full)
            
            # Method @see references
            for see in m.get('see', m.get('seeAlso', [])):
                see_name = see.split('#')[0].split('.')[-1] if see else ''
                if see_name:
                    if see_name not in xref['related']:
                        xref['related'][see_name] = []
                    if method_full not in xref['related'][see_name]:
                        xref['related'][see_name].append(method_full)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(xref, f, indent=2, ensure_ascii=False)
    
    file_size = os.path.getsize(output_file)
    print(f"\nCross-reference written to {output_file} ({file_size / 1024:.1f} KB)")
    print(f"  Types returned by methods: {len(xref['returns_type'])}")
    print(f"  Types accepted as params: {len(xref['accepts_type'])}")
    print(f"  Base classes extended: {len(xref['extends_class'])}")
    print(f"  Interfaces implemented: {len(xref['implements_interface'])}")
    print(f"  Related types (via @see): {len(xref['related'])}")


def main():
    parser = argparse.ArgumentParser(
        description='Extract Java API documentation for AI coding agents',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python java_api_extractor.py /path/to/java/project -o api_docs.json

  # With verbose output to see which file is being processed
  python java_api_extractor.py /path/to/project -o api.json --verbose

  # With timeout per file (default 30s)
  python java_api_extractor.py /path/to/project -o api.json --timeout 10

  # Skip files larger than 500KB
  python java_api_extractor.py /path/to/project -o api.json --max-file-size 512000

  # Full debugging - verbose with short timeout
  python java_api_extractor.py /path/to/project -o api.json --verbose --timeout 5

  # Compact mode (default) - minimal tokens
  python java_api_extractor.py /path/to/project -o api.json --compact

  # Full mode - more readable
  python java_api_extractor.py /path/to/project -o api.json --full

  # Exclude test files and build directories
  python java_api_extractor.py /path/to/project -o api.json -e "test" "build" "target"

  # Generate all reports (index, deprecation, cross-reference)
  python java_api_extractor.py /path/to/project -o api.json --with-index --deprecation-report --cross-reference

  # Skip @see and examples to reduce file size
  python java_api_extractor.py /path/to/project -o api.json --no-see --no-examples
        """
    )
    
    parser.add_argument('directory', help='Directory containing Java source files')
    parser.add_argument('-o', '--output', default='api_docs.json',
                       help='Output JSON file (default: api_docs.json)')
    parser.add_argument('--compact', action='store_true', default=True,
                       help='Use compact format (default)')
    parser.add_argument('--full', action='store_true',
                       help='Use full format (more readable, more tokens)')
    parser.add_argument('-e', '--exclude', nargs='+', default=[],
                       help='Patterns to exclude (regex)')
    parser.add_argument('--include-private', action='store_true',
                       help='Include private members')
    parser.add_argument('--exclude-protected', action='store_true',
                       help='Exclude protected members')
    parser.add_argument('--with-index', action='store_true',
                       help='Generate separate index file')
    parser.add_argument('--deprecation-report', action='store_true',
                       help='Generate separate deprecation report')
    parser.add_argument('--cross-reference', action='store_true',
                       help='Generate cross-reference file showing type relationships')
    parser.add_argument('--no-deprecation-info', action='store_true',
                       help='Exclude deprecation information from output')
    parser.add_argument('--no-examples', action='store_true',
                       help='Exclude code examples from output')
    parser.add_argument('--no-see', action='store_true',
                       help='Exclude @see references from output')
    parser.add_argument('-w', '--workers', type=int, default=4,
                       help='Number of worker threads (default: 4)')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Verbose output - show each file being processed')
    parser.add_argument('-t', '--timeout', type=int, default=30,
                       help='Timeout per file in seconds (default: 30)')
    parser.add_argument('--max-file-size', type=int, default=0,
                       help='Maximum file size in bytes to process (0 = no limit)')
    
    args = parser.parse_args()
    
    default_excludes = [
        r'[\\/]test[\\/]',
        r'[\\/]tests[\\/]',
        r'[\\/]build[\\/]',
        r'[\\/]target[\\/]',
        r'[\\/]\.git[\\/]',
        r'[\\/]node_modules[\\/]',
        r'package-info\.java$',
        r'module-info\.java$'
    ]
    
    exclude_patterns = default_excludes + args.exclude
    
    generator = APIDocumentationGenerator(
        include_private=args.include_private,
        include_protected=not args.exclude_protected,
        compact_mode=not args.full,
        include_deprecated_info=not args.no_deprecation_info,
        include_examples=not args.no_examples,
        include_see_also=not args.no_see,
        max_workers=args.workers,
        timeout=args.timeout,
        max_file_size=args.max_file_size,
        verbose=args.verbose
    )
    
    print(f"Processing Java files in: {args.directory}")
    print(f"Output format: {'Full' if args.full else 'Compact'}")
    print(f"Timeout per file: {args.timeout} seconds")
    print(f"Max file size: {'No limit' if args.max_file_size == 0 else f'{args.max_file_size / 1024:.1f} KB'}")
    print(f"Verbose: {args.verbose}")
    print(f"Include examples: {not args.no_examples}")
    print(f"Include @see: {not args.no_see}")
    print(f"Excluding patterns: {len(exclude_patterns)}")
    print("")
    
    result = generator.process_directory(
        args.directory,
        args.output,
        exclude_patterns
    )
    
    print(f"\n=== Summary ===")
    print(f"Total files found: {result['total_files']}")
    print(f"Successfully parsed: {result['successful']}")
    print(f"Deprecated items found: {result['deprecated_items']}")
    print(f"Errors: {result['errors']}")
    print(f"Timeouts: {result['timeouts']}")
    print(f"Skipped (size): {result['skipped']}")
    print(f"Time elapsed: {result['elapsed_seconds']:.1f} seconds")
    
    if result['error_details']:
        print(f"\nFirst few errors:")
        for err in result['error_details'][:5]:
            print(f"  - {err['file']}: {err['error']}")
    
    if result['timeout_details']:
        print(f"\nFiles that timed out:")
        for t in result['timeout_details']:
            print(f"  - {t['file']} (>{t['timeout']}s)")
    
    if result['skipped_details']:
        print(f"\nFiles skipped due to size:")
        for s in result['skipped_details'][:5]:
            print(f"  - {s['file']}: {s['reason']}")
    
    # Load output for additional reports
    if args.with_index or args.deprecation_report or args.cross_reference:
        with open(args.output, 'r', encoding='utf-8') as f:
            api_docs = json.load(f)
    
    if args.with_index:
        index_file = args.output.replace('.json', '_index.json')
        create_index_file(api_docs, index_file)
    
    if args.deprecation_report:
        dep_file = args.output.replace('.json', '_deprecated.json')
        create_deprecation_report(api_docs, dep_file)
    
    if args.cross_reference:
        xref_file = args.output.replace('.json', '_xref.json')
        create_cross_reference(api_docs, xref_file)


if __name__ == '__main__':
    main()