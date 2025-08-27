def smart_text_splitter(text, chunk_size, chunk_overlap, separators=["\n\n", "\n", ". ", " ", ""]):  
    """  
    Intelligently splits text into chunks of specified size, attempting to split at natural boundaries
    
    Parameters:  
        text (str): The text to be split  
        chunk_size (int): Maximum number of characters in each chunk  
        chunk_overlap (int): Number of overlapping characters between adjacent chunks  
        separators (list): List of separators in order of priority  
    
    Returns:  
        list: List of text chunks  
    """  
    if chunk_size <= 0:  
        raise ValueError("chunk_size must be greater than 0")  
    
    if chunk_overlap >= chunk_size:  
        raise ValueError("chunk_overlap must be less than chunk_size")  
    
    if not text:  
        return []  
    
    # Function: Split text using the given separator  
    def split_text(text, separator):  
        if separator:  
            return text.split(separator)  
        else:  
            # If separator is an empty string, split by character  
            return list(text)  
    
    # Recursive function: Split text by priority of separators in the list  
    def _split_by_separators(text, separators, current_separator_idx=0):  
        if current_separator_idx >= len(separators):  
            # All separators tried, return character-level split  
            return list(text)  
        
        separator = separators[current_separator_idx]  
        segments = split_text(text, separator)  
        
        # If the current separator didn't produce any split, try the next separator  
        if len(segments) == 1 and segments[0] == text:  
            return _split_by_separators(text, separators, current_separator_idx + 1)  
        
        # Recombine with separators to preserve them in the result  
        if separator:  
            segments = [segments[0]] + [separator + segment for segment in segments[1:]]  
        
        # Recursively process segments that are still too large  
        final_segments = []  
        for segment in segments:  
            if len(segment) > chunk_size:  
                # Continue splitting with the next level separator  
                subsegments = _split_by_separators(segment, separators, current_separator_idx + 1)  
                final_segments.extend(subsegments)  
            else:  
                final_segments.append(segment)  
        
        return final_segments  
    
    # Get all possible paragraphs from the text  
    segments = _split_by_separators(text, separators)  
    
    # Combine paragraphs into chunks  
    chunks = []  
    current_chunk = []  
    current_chunk_len = 0  
    
    for segment in segments:  
        segment_len = len(segment)  
        
        # If the current segment is larger than chunk_size, make it a standalone chunk  
        if segment_len > chunk_size:  
            # If current chunk is not empty, add it to the results  
            if current_chunk:  
                chunks.append("".join(current_chunk))  
                
            # Add the oversized segment, which may exceed chunk_size  
            chunks.append(segment)  
            current_chunk = []  
            current_chunk_len = 0  
        
        # If adding the current segment would make the chunk exceed the size limit  
        elif current_chunk_len + segment_len > chunk_size:  
            # Save the current chunk  
            current_text = "".join(current_chunk)
            chunks.append(current_text)  
            
            # Start a new chunk, including the overlap portion
            # Fix: Get the last chunk_overlap characters directly from the joined text
            if chunk_overlap > 0:
                overlap_text = current_text[-chunk_overlap:]
                current_chunk = [overlap_text, segment]
                current_chunk_len = len(overlap_text) + segment_len
            else:
                current_chunk = [segment]
                current_chunk_len = segment_len
        
        # Otherwise, add the segment to the current chunk  
        else:  
            current_chunk.append(segment)  
            current_chunk_len += segment_len  
    
    # Add the last chunk (if any)  
    if current_chunk:  
        chunks.append("".join(current_chunk))  
    
    return chunks  


if __name__ == "__main__":
    text = "Hello, world! This is a test. This is another test. This is a third test."
    for i, chunk in enumerate(smart_text_splitter(text, 30, 10)):
        print(f"Chunk {i} (length {len(chunk)}): '{chunk}'")
