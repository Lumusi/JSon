#!/usr/bin/env python3
"""
Java API Extractor - Combined Source Mode
Combines all Java files into a single minified file for AI context.
"""

import os
import re
import json
import argparse
from pathlib import Path
from typing import List, Set
from concurrent.futures import ThreadPoolExecutor, as_completed


class JavaMinifier:
    """Minifies Java source code while preserving readability for AI."""
    
    def __init__(self, 
                 keep_comments: bool = False,
                 keep_private: bool = False,
                 keep_javadoc: bool = True,
                 keep_deprecated_javadoc: bool = True,
                 single_line: bool = False):
        self.keep_comments = keep_comments
        self.keep_private = keep_private
        self.keep_javadoc = keep_javadoc
        self.keep_deprecated_javadoc = keep_deprecated_javadoc
        self.single_line = single_line
    
    def minify(self, content: str, file_path: str = "") -> str:
        """Minify Java source code."""
        
        # Remove single-line comments (unless keeping all comments)
        if not self.keep_comments:
            content = re.sub(r'(?<!:)//.*$', '', content, flags=re.MULTILINE)
        
        # Handle JavaDoc and block comments
        if not self.keep_comments:
            content = self._handle_javadoc(content)
        
        # Remove private members if requested
        if not self.keep_private:
            content = self._remove_private_members(content)
        
        # Remove excessive blank lines (keep max 1 blank line)
        content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content)
        
        # Remove trailing whitespace
        content = re.sub(r'[ \t]+$', '', content, flags=re.MULTILINE)
        
        # Optionally collapse to single line (very aggressive, not recommended)
        if self.single_line:
            content = self._collapse_to_single_line(content)
        else:
            # Just remove extra indentation
            content = self._normalize_indentation(content)
        
        return content.strip()
    
    def _handle_javadoc(self, content: str) -> str:
        """Keep only important JavaDoc, remove regular block comments."""
        
        if self.keep_javadoc:
            # Keep JavaDoc but simplify it
            def simplify_javadoc(match):
                javadoc = match.group(0)
                
                # Check if it's a deprecation notice
                if self.keep_deprecated_javadoc and '@deprecated' in javadoc.lower():
                    return self._simplify_javadoc_block(javadoc, keep_all=True)
                
                # Check if it has important tags
                important_tags = ['@deprecated', '@param', '@return', '@see', '@throws']
                if any(tag in javadoc for tag in important_tags):
                    return self._simplify_javadoc_block(javadoc, keep_all=False)
                
                # For other JavaDoc, keep only first sentence
                return self._simplify_javadoc_block(javadoc, keep_all=False)
            
            # Replace JavaDoc comments
            content = re.sub(r'/\*\*[\s\S]*?\*/', simplify_javadoc, content)
        else:
            # Remove all JavaDoc
            content = re.sub(r'/\*\*[\s\S]*?\*/', '', content)
        
        # Remove regular block comments
        content = re.sub(r'/\*(?!\*)[\s\S]*?\*/', '', content)
        
        return content
    
    def _simplify_javadoc_block(self, javadoc: str, keep_all: bool = False) -> str:
        """Simplify a JavaDoc block to reduce tokens."""
        lines = javadoc.split('\n')
        result = ['/**']
        
        # Extract main description (first sentence or paragraph)
        description_lines = []
        in_description = True
        
        for line in lines[1:-1]:  # Skip /** and */
            line = line.strip()
            if line.startswith('*'):
                line = line[1:].strip()
            
            if line.startswith('@'):
                in_description = False
                
                # Keep important tags
                if keep_all or any(line.startswith(tag) for tag in ['@deprecated', '@param', '@return', '@see']):
                    # Simplify tag content
                    result.append(f" * {self._simplify_tag_line(line)}")
            elif in_description and line:
                description_lines.append(line)
        
        # Add simplified description
        if description_lines:
            desc = ' '.join(description_lines)
            # Take first sentence or first 100 chars
            if not keep_all:
                first_sentence = re.split(r'[.!?]\s', desc)[0]
                desc = first_sentence[:100] if len(first_sentence) > 100 else first_sentence
            result.insert(1, f" * {desc}")
        
        result.append(' */')
        return '\n'.join(result) if result else ''
    
    def _simplify_tag_line(self, line: str) -> str:
        """Simplify a JavaDoc tag line."""
        # Remove HTML tags
        line = re.sub(r'<[^>]+>', '', line)
        # Simplify {@link} and {@code}
        line = re.sub(r'\{@link\s+([^}]+)\}', r'\1', line)
        line = re.sub(r'\{@code\s+([^}]+)\}', r'\1', line)
        # Limit length
        if len(line) > 150:
            line = line[:147] + "..."
        return line
    
    def _remove_private_members(self, content: str) -> str:
        """Remove private methods and fields."""
        
        # Remove private methods (including body)
        content = re.sub(
            r'private\s+(?:static\s+)?(?:final\s+)?[\w<>,\[\]\s]+\s+\w+\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}',
            '',
            content
        )
        
        # Remove private fields
        content = re.sub(
            r'private\s+(?:static\s+)?(?:final\s+)?[\w<>,\[\]\s]+\s+\w+(?:\s*=\s*[^;]+)?;',
            '',
            content
        )
        
        return content
    
    def _normalize_indentation(self, content: str) -> str:
        """Normalize indentation to reduce unnecessary spaces."""
        lines = content.split('\n')
        result = []
        
        for line in lines:
            # Replace tabs with spaces
            line = line.replace('\t', '    ')
            # Remove excessive indentation (keep max 4 levels)
            if line.startswith('        '):  # 8+ spaces
                # Count leading spaces
                stripped = line.lstrip()
                spaces = len(line) - len(stripped)
                # Reduce to max 16 spaces (4 levels)
                if spaces > 16:
                    line = '    ' * 4 + stripped
            result.append(line)
        
        return '\n'.join(result)
    
    def _collapse_to_single_line(self, content: str) -> str:
        """Collapse code to single line (very aggressive, loses readability)."""
        # Remove all newlines within braces
        content = re.sub(r'\s+', ' ', content)
        content = re.sub(r'\s*([{}();,])\s*', r'\1', content)
        return content


class CombinedSourceGenerator:
    """Generates a single combined source file from multiple Java files."""
    
    def __init__(self,
                 minifier: JavaMinifier,
                 include_imports: bool = True,
                 group_by_package: bool = True,
                 add_separators: bool = True,
                 max_file_size: int = 0):
        self.minifier = minifier
        self.include_imports = include_imports
        self.group_by_package = group_by_package
        self.add_separators = add_separators
        self.max_file_size = max_file_size
    
    def generate(self, directory: str, output_file: str, 
                exclude_patterns: List[str] = None) -> dict:
        """Generate combined source file."""
        
        exclude_patterns = exclude_patterns or []
        java_files = self._find_java_files(directory, exclude_patterns)
        
        print(f"Found {len(java_files)} Java files to combine...")
        
        # Parse and minify all files
        parsed_files = []
        skipped = []
        
        for file_path in java_files:
            try:
                # Check file size
                if self.max_file_size > 0:
                    file_size = os.path.getsize(file_path)
                    if file_size > self.max_file_size:
                        skipped.append(file_path)
                        continue
                
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # Extract package and class info
                package = self._extract_package(content)
                class_name = self._extract_class_name(content)
                
                # Minify
                minified = self.minifier.minify(content, file_path)
                
                parsed_files.append({
                    'path': file_path,
                    'package': package,
                    'class_name': class_name,
                    'content': minified,
                    'original_size': len(content),
                    'minified_size': len(minified)
                })
                
            except Exception as e:
                print(f"Error processing {file_path}: {e}")
                skipped.append(file_path)
        
        # Sort files
        if self.group_by_package:
            parsed_files.sort(key=lambda x: (x['package'], x['class_name']))
        else:
            parsed_files.sort(key=lambda x: x['class_name'])
        
        # Combine into single file
        combined_content = self._combine_files(parsed_files)
        
        # Write output
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(combined_content)
        
        # Calculate stats
        original_total = sum(f['original_size'] for f in parsed_files)
        minified_total = sum(f['minified_size'] for f in parsed_files)
        final_size = len(combined_content)
        
        print(f"\n=== Combined Source Statistics ===")
        print(f"Files processed: {len(parsed_files)}")
        print(f"Files skipped: {len(skipped)}")
        print(f"Original total size: {original_total / 1024:.1f} KB")
        print(f"Minified total size: {minified_total / 1024:.1f} KB")
        print(f"Final combined size: {final_size / 1024:.1f} KB")
        print(f"Reduction: {(1 - final_size / original_total) * 100:.1f}%")
        print(f"Output: {output_file}")
        
        # Estimate tokens (rough: ~4 chars per token for code)
        estimated_tokens = final_size // 4
        print(f"Estimated tokens: ~{estimated_tokens:,}")
        
        return {
            'files_processed': len(parsed_files),
            'files_skipped': len(skipped),
            'original_size': original_total,
            'minified_size': minified_total,
            'final_size': final_size,
            'reduction_percent': (1 - final_size / original_total) * 100,
            'estimated_tokens': estimated_tokens
        }
    
    def _find_java_files(self, directory: str, exclude_patterns: List[str]) -> List[str]:
        """Find all Java files."""
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
    
    def _extract_package(self, content: str) -> str:
        """Extract package name from Java content."""
        match = re.search(r'^\s*package\s+([\w.]+)\s*;', content, re.MULTILINE)
        return match.group(1) if match else ""
    
    def _extract_class_name(self, content: str) -> str:
        """Extract main class name from Java content."""
        match = re.search(
            r'(?:public\s+)?(?:class|interface|enum|record)\s+(\w+)',
            content
        )
        return match.group(1) if match else "Unknown"
    
    def _combine_files(self, parsed_files: List[dict]) -> str:
        """Combine all parsed files into single content."""
        sections = []
        
        # Collect all unique imports
        all_imports = set()
        if self.include_imports:
            for f in parsed_files:
                imports = re.findall(
                    r'^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;',
                    f['content'],
                    re.MULTILINE
                )
                all_imports.update(imports)
        
        # Add header
        sections.append("// Combined Java Source - Generated for AI Context")
        sections.append("// Total files: {}".format(len(parsed_files)))
        sections.append("")
        
        # Add combined imports
        if all_imports:
            sections.append("// Combined Imports")
            for imp in sorted(all_imports):
                sections.append(f"import {imp};")
            sections.append("")
        
        # Group by package if requested
        if self.group_by_package:
            current_package = None
            for f in parsed_files:
                # Add package separator
                if f['package'] != current_package:
                    if current_package is not None:
                        sections.append("")
                    sections.append(f"// ========== Package: {f['package']} ==========")
                    sections.append("")
                    current_package = f['package']
                
                # Add file separator
                if self.add_separators:
                    sections.append(f"// ----- {f['class_name']} ({f['package']}) -----")
                
                # Add minified content (remove package and import statements)
                content = self._remove_package_and_imports(f['content'])
                sections.append(content)
                sections.append("")
        else:
            # Just concatenate
            for f in parsed_files:
                if self.add_separators:
                    sections.append(f"// ----- {f['class_name']} -----")
                content = self._remove_package_and_imports(f['content'])
                sections.append(content)
                sections.append("")
        
        return '\n'.join(sections)
    
    def _remove_package_and_imports(self, content: str) -> str:
        """Remove package and import statements from content."""
        # Remove package statement
        content = re.sub(r'^\s*package\s+[\w.]+\s*;', '', content, flags=re.MULTILINE)
        # Remove import statements
        content = re.sub(r'^\s*import\s+(?:static\s+)?[\w.]+(?:\.\*)?\s*;', '', content, flags=re.MULTILINE)
        # Remove excessive blank lines
        content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content)
        return content.strip()


def main():
    parser = argparse.ArgumentParser(
        description='Java API Extractor - Combined Source Mode',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Combine all Java files with default minification
  python java_combiner.py /path/to/project -o combined.java --combined-source

  # Maximum minification (no comments, no private members)
  python java_combiner.py /path/to/project -o combined.java --combined-source --strip-all

  # Keep important JavaDoc only
  python java_combiner.py /path/to/project -o combined.java --combined-source --keep-javadoc

  # Keep everything, just combine files
  python java_combiner.py /path/to/project -o combined.java --combined-source --no-minify

  # Exclude test files
  python java_combiner.py /path/to/project -o combined.java --combined-source -e "test" "Test"

  # Skip files larger than 500KB (e.g., ItemID.java)
  python java_combiner.py /path/to/project -o combined.java --combined-source --max-file-size 512000
        """
    )
    
    parser.add_argument('directory', help='Directory containing Java source files')
    parser.add_argument('-o', '--output', default='combined.java',
                       help='Output combined file (default: combined.java)')
    
    # Combined source mode
    parser.add_argument('--combined-source', action='store_true',
                       help='Generate combined source file (required)')
    
    # Minification options
    parser.add_argument('--no-minify', action='store_true',
                       help='Do not minify, just combine files')
    parser.add_argument('--strip-all', action='store_true',
                       help='Maximum minification: remove all comments, private members')
    parser.add_argument('--keep-javadoc', action='store_true', default=True,
                       help='Keep JavaDoc comments (default: true)')
    parser.add_argument('--no-javadoc', action='store_true',
                       help='Remove all JavaDoc comments')
    parser.add_argument('--keep-comments', action='store_true',
                       help='Keep all comments (overrides other options)')
    parser.add_argument('--keep-private', action='store_true',
                       help='Keep private members')
    parser.add_argument('--single-line', action='store_true',
                       help='Collapse to single line (not recommended, loses readability)')
    
    # Organization options
    parser.add_argument('--no-imports', action='store_true',
                       help='Do not include combined import statements')
    parser.add_argument('--no-grouping', action='store_true',
                       help='Do not group by package')
    parser.add_argument('--no-separators', action='store_true',
                       help='Do not add file/package separators')
    
    # Filtering options
    parser.add_argument('-e', '--exclude', nargs='+', default=[],
                       help='Patterns to exclude (regex)')
    parser.add_argument('--max-file-size', type=int, default=0,
                       help='Skip files larger than N bytes (0 = no limit)')
    
    # Output format options
    parser.add_argument('--stats-json', help='Output statistics to JSON file')
    
    args = parser.parse_args()
    
    if not args.combined_source:
        parser.error("This script requires --combined-source flag")
    
    # Default excludes
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
    
    # Configure minifier
    if args.no_minify:
        keep_comments = True
        keep_private = True
        keep_javadoc = True
    elif args.strip_all:
        keep_comments = False
        keep_private = False
        keep_javadoc = False
    else:
        keep_comments = args.keep_comments
        keep_private = args.keep_private
        keep_javadoc = args.keep_javadoc and not args.no_javadoc
    
    minifier = JavaMinifier(
        keep_comments=keep_comments,
        keep_private=keep_private,
        keep_javadoc=keep_javadoc,
        keep_deprecated_javadoc=True,  # Always keep deprecation info
        single_line=args.single_line
    )
    
    # Configure generator
    generator = CombinedSourceGenerator(
        minifier=minifier,
        include_imports=not args.no_imports,
        group_by_package=not args.no_grouping,
        add_separators=not args.no_separators,
        max_file_size=args.max_file_size
    )
    
    print(f"Combining Java files from: {args.directory}")
    print(f"Minification: {'Disabled' if args.no_minify else 'Maximum' if args.strip_all else 'Standard'}")
    print(f"Keep JavaDoc: {keep_javadoc}")
    print(f"Keep private: {keep_private}")
    print(f"Excluding patterns: {len(exclude_patterns)}")
    print("")
    
    # Generate combined source
    stats = generator.generate(
        args.directory,
        args.output,
        exclude_patterns
    )
    
    # Save stats if requested
    if args.stats_json:
        with open(args.stats_json, 'w') as f:
            json.dump(stats, f, indent=2)
        print(f"\nStatistics saved to: {args.stats_json}")


if __name__ == '__main__':
    main()