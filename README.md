# NIH_Hyperlinks_Remover
NIH does not allow hyperlinks, except for those created by SciENcv, unless otherwise specified in the NOFO per NOT-OD-20-174.
Going forward, the UMN Department of Psychiatry will be using this Python program to remove hyperlinks from proposal PDFs.
The code is provided here for individuals who would like to use the program for their own PDFs or simply review the code.

This Python program removes hyperlinks, removes underlines under hyperlinked text, and changes hyperlinked blue text to black.
At present, due to the difficulty of identifying locations in PDFs, this code imperfectly changes hyperlinked blue text to black.
We have erred on the side of caution, and it is more likely that hyperlinked blue text will remain blue 
than non-hyperlinked blue text will be changed to black.
We will improve this code as workload permits.

A GUI interface collects the file path for the input folder and output folder.
The input folder holds the original proposal PDFs that contain hyperlinks.
The output folder holds the processed proposal PDFs with hyperlinks removed.

Department grant administrators will manually review processed PDFs to ensure that hyperlinks are removed.
As is already standard practice, the PI will be given a proposal preview for review.
