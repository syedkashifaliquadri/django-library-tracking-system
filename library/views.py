from rest_framework import viewsets, status
from rest_framework.response import Response
from .models import Author, Book, Member, Loan
from .serializers import AuthorSerializer, BookSerializer, MemberSerializer, LoanSerializer
from rest_framework.decorators import action
from django.utils import timezone
from .tasks import send_loan_notification
from datetime import timedelta
from .pagination import CustomPageNumberPagination
from django.db.models import Count, Q


class AuthorViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer


class BookViewSet(viewsets.ModelViewSet):
    serializer_class = BookSerializer
    queryset = Book.objects.all()
    pagination_class = CustomPageNumberPagination

    def get_queryset(self):
        return Book.objects.select_related('author').all()

    @action(detail=True, methods=['post'])
    def loan(self, request, pk=None):
        book = self.get_object()
        if book.available_copies < 1:
            return Response({'error': 'No available copies.'}, status=status.HTTP_400_BAD_REQUEST)
        member_id = request.data.get('member_id')
        try:
            member = Member.objects.get(id=member_id)
        except Member.DoesNotExist:
            return Response({'error': 'Member does not exist.'}, status=status.HTTP_400_BAD_REQUEST)
        loan = Loan.objects.create(book=book, member=member)
        book.available_copies -= 1
        book.save()
        send_loan_notification.delay(loan.id)
        return Response({'status': 'Book loaned successfully.'}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def return_book(self, request, pk=None):
        book = self.get_object()
        member_id = request.data.get('member_id')
        try:
            loan = Loan.objects.get(book=book, member__id=member_id, is_returned=False)
        except Loan.DoesNotExist:
            return Response({'error': 'Active loan does not exist.'}, status=status.HTTP_400_BAD_REQUEST)
        loan.is_returned = True
        loan.return_date = timezone.now().date()
        loan.save()
        book.available_copies += 1
        book.save()
        return Response({'status': 'Book returned successfully.'}, status=status.HTTP_200_OK)


class MemberViewSet(viewsets.ModelViewSet):
    queryset = Member.objects.all()
    serializer_class = MemberSerializer

    @action(detail=False, methods=['get'], url_path='top-active')
    def top_active_member(self, request):
        top_member = (
            Member.objects.annotate(active_loans=Count('load', filter=Q(loan__is_returned=False))
                                    )
            .filter(active_loans__gt=0).order_by('-active_loans')[:5]
        )

        data = [
            {
                'id': member.id,
                'username': member.user.username,
                'email': member.user.email,
                'active_loans': member.active_loans
            }
            for member in top_member
        ]

        return Response(data)


class LoanViewSet(viewsets.ModelViewSet):
    queryset = Loan.objects.all()
    serializer_class = LoanSerializer

    @action(detail=True, methods=['post'])
    def extend_due_date(self, request, pk=None):
        try:
            loan = Loan.objects.get(pk=pk)
        except Loan.DoesNotExist:
            return Response({'error': 'loan does not exist.'}, status=status.HTTP_400_BAD_REQUEST)

        if loan.due_date < timezone.now().date():
            return Response({'error': 'loan is already overdue.'}, status=status.HTTP_400_BAD_REQUEST)

        additional_days = request.data.get('additional_days')
        if not str(additional_days).isdigit() or int(additional_days) <= 0:
            return Response({'error': 'Invalid Additional Days number.'}, status=status.HTTP_400_BAD_REQUEST)

        loan.due_date += timedelta(days=int(additional_days))
        loan.save()

        return Response({'message': 'Due date extended', 'new_due_date': loan.due_date})
