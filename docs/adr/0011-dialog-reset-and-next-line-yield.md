# Next line yields per gesture, and dialog reset quiets the robot without ending the conversation

A later line stops the one already speaking. That made a tap skip a line the visitor was still hearing. Putting the guard on the dialog would have made every next-line gesture wait. The guard is on the gesture, on by default, including when an older binding has no choice stored. While it yields, the press does nothing until the words end. A long press can turn it off and cut in. Previous line and repeat line still cut in.

Dialog reset clears the shared position and takes the voice: the dialog line, a fixed phrase, and a conversation reply, including one not yet spoken. The conversation stays up and its context stays. With no active dialog the press does nothing. The alternative was to end the conversation, which would throw away the dialogue the model is using.
