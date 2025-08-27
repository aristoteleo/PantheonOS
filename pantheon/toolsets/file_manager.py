from pathlib import Path
import shutil
import base64
import io
from datetime import datetime

from PIL import Image


from ..toolset import ToolSet, tool


class FileManagerToolSetBase(ToolSet):
    """Base class for file manager toolset.
    This class is not a toolset itself, but a base class for other file manager toolsets.
    It provides the basic file manager functionality, including:
        - list files
        - create directory
        - delete directory
        - delete file
        - move file
        - read file

    Args:
        name: The name of the toolset.
        path: The path to the directory to manage.
        worker_params: The parameters for the worker.
        black_list: The list of files to ignore.
        **kwargs: Additional keyword arguments.
    """

    def __init__(
            self,
            name: str,
            path: str | Path,
            worker_params: dict | None = None,
            black_list: list[str] | None = None,
            **kwargs,
            ):
        super().__init__(name, worker_params, **kwargs)
        self.path = Path(path)
        self.black_list = black_list or []

    @tool
    async def list_files(self, sub_dir: str | None = None) -> dict:
        """List all files in the directory."""
        if not self.path.exists():
            return {"success": False, "error": "Directory does not exist"}
        if (sub_dir is not None) and ('..' in sub_dir):
            return {"success": False, "error": "Sub directory cannot contain '..'"}
        if sub_dir is None or sub_dir == "":
            files = list(self.path.glob("*"))
        else:
            files = list(self.path.glob(f"{sub_dir}/*"))
        return {
            "success": True,
            "files": [
                {
                    "name": file.name,
                    "size": file.stat().st_size if file.is_file() else 0,
                    "type": "file" if file.is_file() else "directory",
                    "last_modified": datetime.fromtimestamp(file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                }
                for file in files
                if file.name not in self.black_list
            ],
        }

    @tool
    async def create_directory(self, sub_dir: str):
        """Create a new directory."""
        if '..' in sub_dir:
            return {"success": False, "error": "Sub directory cannot contain '..'"}
        new_dir = self.path / sub_dir
        new_dir.mkdir(parents=True, exist_ok=True)
        return {"success": True}

    @tool
    async def delete_directory(self, sub_dir: str):
        """Delete a directory and all its contents recursively."""
        if '..' in sub_dir:
            return {"success": False, "error": "Sub directory cannot contain '..'"}
        dir_path = self.path / sub_dir
        if not dir_path.exists():
            return {"success": False, "error": "Directory does not exist"}
        if not dir_path.is_dir():
            return {"success": False, "error": "Path is not a directory"}
        shutil.rmtree(dir_path)
        return {"success": True}

    @tool
    async def delete_file(self, file_path: str):
        """Delete a file."""
        if '..' in file_path:
            return {"success": False, "error": "File path cannot contain '..'"}
        path = self.path / file_path
        if not path.exists():
            return {"success": False, "error": "File does not exist"}
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        return {"success": True}

    @tool
    async def move_file(self, old_path: str, new_path: str):
        """Move a file."""
        if '..' in old_path:
            return {"success": False, "error": "Old path cannot contain '..'"}
        if '..' in new_path:
            return {"success": False, "error": "New path cannot contain '..'"}
        old_path = self.path / old_path
        if not old_path.exists():
            return {"success": False, "error": "Old path does not exist"}
        new_path = self.path / new_path
        shutil.move(old_path, new_path)
        return {"success": True}


def path_to_image_url(path: str) -> str:
    img = Image.open(path)
    with io.BytesIO() as buffer:
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"


class FileManagerToolSet(FileManagerToolSetBase):
    """File manager toolset.
    This class is a toolset that provides the basic file manager functionality, including:
    - list files
    - create directory
    - delete directory
    - delete file
    - move file
    - read file
    - read pdf (Convert pdf to text)
    - observe image

    Args:
        name: The name of the toolset.
        path: The path to the directory to manage.
        worker_params: The parameters for the worker.
        black_list: The list of files to ignore.
        **kwargs: Additional keyword arguments.
    """

    @tool
    async def list_file_tree(self, sub_dir: str | None = None) -> list[dict]:
        """List all files in the directory recursively."""
        if not self.path.exists():
            return {"success": False, "error": "Directory does not exist"}
        if (sub_dir is not None) and ('..' in sub_dir):
            return {"success": False, "error": "Sub directory cannot contain '..'"}

        def _list_tree(path: Path) -> dict:
            """Helper function to recursively build the tree structure."""
            result = {
                "name": path.name,
                "type": "directory" if path.is_dir() else "file",
                "size": path.stat().st_size if path.is_file() else 0,
            }
            if path.is_dir():
                result["children"] = []
                for item in sorted(path.iterdir()):
                    result["children"].append(_list_tree(item))
            return result

        target_path = self.path / sub_dir if sub_dir else self.path
        if not target_path.exists():
            return {"success": False, "error": "Target directory does not exist"}

        return _list_tree(target_path)

    @tool
    async def read_file(self, file_path: str, first_n_lines: int | None = None) -> dict:
        """Read a text file.
        
        Args:
            file_path: The path to the file to read.
            first_n_lines: The number of lines to read from the file. If not provided, the entire file will be read.
        """
        if '..' in file_path:
            return {"success": False, "error": "File path cannot contain '..'"}
        file_path = self.path / file_path
        if not file_path.exists():
            return {"success": False, "error": "File does not exist"}
        with open(file_path, "r") as f:
            if first_n_lines is not None:
                lines = f.readlines()[:first_n_lines]
                # Join lines without adding extra newlines since readlines() keeps original line endings
                content = "".join(lines).rstrip('\n')
            else:
                content = f.read()
            return {
                "success": True,
                "content": content,
                "format": file_path.suffix.lower(),
            }

    @tool
    async def write_file(self, file_path: str, content: str) -> dict:
        """Write text to a file, note this function only supports text files. """
        if '..' in file_path:
            return {"success": False, "error": "File path cannot contain '..'"}
        file_path = self.path / file_path
        if not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w") as f:
            f.write(content)
        return {"success": True}

    @tool
    async def observe_images(self, question: str, image_paths: list[str]) -> str:
        """Observe images and answer a question about them.
        
        Args:
            question: The question to answer.
            image_paths: The paths to the images to view."""
        query_msg = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": question,
                },
            ],
        }
        images_base64_uri = []
        for img_path in image_paths:
            ipath = self.path / img_path
            base64_uri = path_to_image_url(ipath)
            images_base64_uri.append(base64_uri)
            query_msg["content"].append({
                "type": "image_url",
                "image_url": {"url": base64_uri},
            })
        res = {
            "success": True,
            "inner_call": {
                "name": "__agent_run__",
                "args": [query_msg],
                "result_field": "content",
            },
            "hidden_to_model": ["base64_uri", "inner_call_args"],
        }
        return res

    @tool
    async def read_pdf(self, pdf_path: str) -> dict:
        """Read a PDF file and return the text inside it.
        
        Args:
            pdf_path: The path to the PDF file to read.
            
        Returns:
            dict: Success status, content, and metadata about the PDF.
        """
        if '..' in pdf_path:
            return {"success": False, "error": "File path cannot contain '..'"}
            
        file_path = self.path / pdf_path
        
        # Check if file exists
        if not file_path.exists():
            return {"success": False, "error": "PDF file does not exist"}
            
        # Check if it's actually a file
        if not file_path.is_file():
            return {"success": False, "error": "Path is not a file"}
            
        # Check if it has a PDF extension
        if file_path.suffix.lower() != '.pdf':
            return {"success": False, "error": "File is not a PDF (wrong extension)"}
            
        try:
            # Try to import pymupdf
            import pymupdf
        except ImportError:
            return {
                "success": False, 
                "error": "pymupdf library not installed. Install with: pip install pymupdf"
            }
            
        try:
            # Open and read the PDF
            texts = []
            page_count = 0
            
            with pymupdf.open(str(file_path)) as doc:
                page_count = len(doc)
                
                # Check if PDF is password protected
                if doc.needs_pass:
                    return {
                        "success": False, 
                        "error": "PDF is password protected and cannot be read"
                    }
                
                # Extract text from each page
                for page_num, page in enumerate(doc):
                    try:
                        page_text = page.get_text()
                        if page_text.strip():  # Only add non-empty pages
                            texts.append(f"--- Page {page_num + 1} ---\n{page_text}")
                    except Exception as e:
                        texts.append(f"--- Page {page_num + 1} (Error reading) ---\nError: {str(e)}")
                        
            # Combine all text
            full_text = "\n\n".join(texts)
            
            return {
                "success": True,
                "content": full_text,
                "format": ".pdf",
                "metadata": {
                    "total_pages": page_count,
                    "file_size": file_path.stat().st_size,
                    "pages_with_text": len([t for t in texts if not t.startswith("--- Page") or "Error" not in t])
                }
            }
            
        except pymupdf.FileDataError:
            return {"success": False, "error": "Invalid or corrupted PDF file"}
        except pymupdf.FitzError as e:
            return {"success": False, "error": f"PDF processing error: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Unexpected error reading PDF: {str(e)}"}


__all__ = ["FileManagerToolSet"]
